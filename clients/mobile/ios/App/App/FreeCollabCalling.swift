import UIKit
import AVFoundation
import CallKit
import PushKit
import Security
import Capacitor
import LiveKit

/// Only this narrow call capability enters the Keychain. General sign-in
/// bearers, Matrix recovery material and message contents never enter this path.
private struct FreeCollabCallCredential: Codable {
    let origin: String
    let deviceID: String
    let secret: String
}

private enum FreeCollabCallKeychain {
    private static let service = "eu.freecollab.native-calling"
    private static var query: [String: Any] { [kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: service, kSecAttrAccount as String: "call-only-device"] }
    static func read() -> FreeCollabCallCredential? {
        var values = query; values[kSecReturnData as String] = true; values[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        guard SecItemCopyMatching(values as CFDictionary, &item) == errSecSuccess, let data = item as? Data else { return nil }
        return try? JSONDecoder().decode(FreeCollabCallCredential.self, from: data)
    }
    static func save(_ credential: FreeCollabCallCredential) throws {
        let data = try JSONEncoder().encode(credential)
        var values = query
        values[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        values[kSecValueData as String] = data
        // Do not synchronize or share the capability with another device/app.
        SecItemDelete(query as CFDictionary)
        guard SecItemAdd(values as CFDictionary, nil) == errSecSuccess else { throw FreeCollabCallFailure.unavailable }
    }
    static func clear() { SecItemDelete(query as CFDictionary) }
}

private enum FreeCollabCallFailure: Error { case unavailable }

/// An ephemeral, streaming, bounded HTTP exchange. Redirects are never followed,
/// TLS keeps platform verification, and a response cannot allocate an unbounded
/// buffer. The session retains this delegate until it invalidates on completion.
private final class FreeCollabCallHTTP: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    private var received = Data()
    private var continuation: CheckedContinuation<[String: Any], Error>?
    private var exchange: URLSession?
    private var status = 0
    static func request(_ credential: FreeCollabCallCredential, _ path: String,
                        method: String = "POST", body: [String: Any]? = [:]) async throws -> [String: Any] {
        guard let url = FreeCollabCallingPolicy.apiURL(origin: credential.origin, path: path) else { throw FreeCollabCallFailure.unavailable }
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 5)
        request.httpMethod = method
        request.setValue("Bearer " + credential.secret, forHTTPHeaderField: "Authorization")
        request.setValue(credential.deviceID, forHTTPHeaderField: "X-FreeCollab-Call-Device")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let body = body { request.httpBody = try JSONSerialization.data(withJSONObject: body) }
        let delegate = FreeCollabCallHTTP()
        return try await withCheckedThrowingContinuation { continuation in
            delegate.continuation = continuation
            let configuration = URLSessionConfiguration.ephemeral
            configuration.httpCookieStorage = nil; configuration.httpShouldSetCookies = false
            configuration.urlCredentialStorage = nil; configuration.waitsForConnectivity = false
            configuration.urlCache = nil; configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
            configuration.timeoutIntervalForRequest = 5; configuration.timeoutIntervalForResource = 5
            delegate.exchange = URLSession(configuration: configuration, delegate: delegate, delegateQueue: nil)
            delegate.exchange?.dataTask(with: request).resume()
        }
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        status = (response as? HTTPURLResponse)?.statusCode ?? 0
        completionHandler((200...299).contains(status) && response.expectedContentLength <= 65536 ? .allow : .cancel)
    }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        if received.count + data.count > 65536 { dataTask.cancel() } else { received.append(data) }
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        let completion = continuation; continuation = nil
        defer { exchange?.finishTasksAndInvalidate(); exchange = nil }
        guard error == nil, (200...299).contains(status), received.count <= 65536 else {
            completion?.resume(throwing: FreeCollabCallFailure.unavailable); return
        }
        if received.isEmpty { completion?.resume(returning: [:]); return }
        guard let value = try? JSONSerialization.jsonObject(with: received) as? [String: Any] else {
            completion?.resume(throwing: FreeCollabCallFailure.unavailable); return
        }
        completion?.resume(returning: value)
    }
}

/// All call state and OS callbacks are serialized on the main queue. The
/// monotonic watchdog and native audio service do not depend on WebView timers.
final class FreeCollabCallManager: NSObject, CXProviderDelegate, PKPushRegistryDelegate, RoomDelegate, @unchecked Sendable {
    static let shared = FreeCollabCallManager()
    private let controller = CXCallController()
    private let provider: CXProvider
    private var registry: PKPushRegistry?
    private var callUUID: UUID?
    private var deliveryID: String?
    private var outgoingRoomID: String?
    private var sessionID: String?
    private var room: Room?
    private var phase = "idle"
    private var muted = false
    private var held = false
    private var audioActivated = false
    private var explicitAnswer = false
    private var mediaReady = false
    private var generation = 0
    private var controlGeneration = 0
    private var lastAuthorized: TimeInterval = 0
    private var heartbeatBusy = false
    private var heartbeatTimer: DispatchSourceTimer?
    private var watchdogTimer: DispatchSourceTimer?
    private var incomingDeadline: DispatchWorkItem?
    private var pendingAction: CXAction?
    private var stoppingMedia = false
    private var teardownID: UUID?
    private let optInKey = "freecollab-native-call-opt-in-origin"

    private override init() {
        let config = CXProviderConfiguration(localizedName: "FreeCollab")
        config.supportsVideo = false; config.maximumCallGroups = 1; config.maximumCallsPerCallGroup = 1
        config.includesCallsInRecents = false; config.supportedHandleTypes = [.generic]
        provider = CXProvider(configuration: config)
        super.init()
        provider.setDelegate(self, queue: .main)
        LiveKitSDK.disableLogging()
        // CallKit exclusively owns audio activation. Merely receiving a push or
        // connecting signalling is never permission to access an audio device.
        AudioManager.shared.audioSession.isAutomaticConfigurationEnabled = false
        AudioManager.shared.isMicrophoneMuted = true
        try? AudioManager.shared.setEngineAvailability(.none)
    }
    static func origin(_ value: String?) -> String? { FreeCollabCallingPolicy.origin(value) }
    private static func validID(_ value: String?) -> String? { FreeCollabCallingPolicy.identifier(value) }
    private static func configuredOrigin() -> String? {
        guard let path = Bundle.main.url(forResource: "capacitor.config", withExtension: "json"),
              let data = try? Data(contentsOf: path), let config = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let server = config["server"] as? [String: Any] else { return nil }
        return origin(server["url"] as? String)
    }
    private func credential() -> FreeCollabCallCredential? {
        guard let configured = Self.configuredOrigin(), UserDefaults.standard.string(forKey: optInKey) == configured,
              let saved = FreeCollabCallKeychain.read(), saved.origin == configured,
              Self.validID(saved.deviceID) != nil,
              saved.secret.range(of: "^[A-Za-z0-9_-]{43}$", options: .regularExpression) != nil else { return nil }
        return saved
    }
    func restore() {
        guard let configured = Self.configuredOrigin(), UserDefaults.standard.string(forKey: optInKey) == configured else { return }
        // AfterFirstUnlock material is deliberately unavailable before the first
        // unlock after a reboot. A locked callback can report/end, never join.
        registerVoIP()
    }
    func configure(origin: String, deviceID: String, secret: String) throws {
        guard let canonical = Self.origin(origin), canonical == Self.configuredOrigin(), Self.validID(deviceID) != nil,
              secret.range(of: "^[A-Za-z0-9_-]{43}$", options: .regularExpression) != nil,
              AVAudioSession.sharedInstance().recordPermission == .granted else { throw FreeCollabCallFailure.unavailable }
        stop(reason: .remoteEnded)
        try FreeCollabCallKeychain.save(FreeCollabCallCredential(origin: canonical, deviceID: deviceID, secret: secret))
        UserDefaults.standard.set(canonical, forKey: optInKey)
        registerVoIP()
        if let token = registry?.pushToken(for: .voIP) { updateVoIP(token) }
    }
    private func registerVoIP() {
        if registry != nil { return }
        let next = PKPushRegistry(queue: .main); next.delegate = self; registry = next; next.desiredPushTypes = [.voIP]
    }
    func status() -> [String: Any] {
        var values: [String: Any] = ["configured": credential() != nil, "phase": phase, "muted": muted, "held": held, "audio_e2ee": false]
        if let credential = credential() { values["device_id"] = credential.deviceID }; return values
    }
    func disable() {
        let captured = credential()
        stop(reason: .remoteEnded)
        // Clear before awaiting network. A delayed revoke must neither restore
        // a logged-out capability nor remove a new account's replacement.
        FreeCollabCallKeychain.clear(); UserDefaults.standard.removeObject(forKey: optInKey)
        registry?.desiredPushTypes = []; registry?.delegate = nil; registry = nil
        if let captured = captured { Task { _ = try? await FreeCollabCallHTTP.request(captured, "device", method: "DELETE", body: nil) } }
    }
    private func updateVoIP(_ token: Data) {
        guard let captured = credential() else { return }
        let value = token.map { String(format: "%02x", $0) }.joined()
        Task { _ = try? await FreeCollabCallHTTP.request(captured, "device/voip", method: "PUT", body: ["token": value]) }
    }
    func pushRegistry(_ registry: PKPushRegistry, didUpdate pushCredentials: PKPushCredentials, for type: PKPushType) {
        guard type == .voIP else { return }; updateVoIP(pushCredentials.token)
    }
    func pushRegistry(_ registry: PKPushRegistry, didInvalidatePushTokenFor type: PKPushType) {
        if type == .voIP { disable() }
    }
    func pushRegistry(_ registry: PKPushRegistry, didReceiveIncomingPushWith payload: PKPushPayload,
                      for type: PKPushType, completion: @escaping () -> Void) {
        guard type == .voIP else { completion(); return }
        let values = payload.dictionaryPayload
        let identifier = Self.validID(values["id"] as? String), device = Self.validID(values["device_id"] as? String)
        let captured = credential(), duplicate = identifier != nil && identifier == deliveryID
        let identifierUUID = duplicate ? (callUUID ?? UUID()) : UUID()
        let update = CXCallUpdate(); update.remoteHandle = CXHandle(type: .generic, value: "FreeCollab call")
        update.localizedCallerName = "FreeCollab call"; update.hasVideo = false
        update.supportsHolding = true; update.supportsGrouping = false; update.supportsUngrouping = false; update.supportsDTMF = false
        // Apple requires immediate CallKit reporting for every VoIP wake. This
        // report always precedes network lookup, even for a stale/invalid push.
        provider.reportNewIncomingCall(with: identifierUUID, update: update) { [weak self] error in
            completion()
            DispatchQueue.main.async {
                guard let self = self else { return }
                if duplicate { return }
                guard error == nil else { return }
                guard values["type"] as? String == "native_call", let identifier = identifier,
                      let captured = captured, captured.deviceID == device, self.phase == "idle", !self.stoppingMedia,
                      self.credential()?.deviceID == captured.deviceID else {
                    self.provider.reportCall(with: identifierUUID, endedAt: Date(), reason: .failed); return
                }
                self.callUUID = identifierUUID; self.deliveryID = identifier; self.phase = "ringing"
                let expected = self.generation
                self.startRingingTimers(expected: expected, captured: captured, delivery: identifier)
                let deadline = DispatchWorkItem { [weak self] in
                    guard let self = self, self.generation == expected, self.phase == "ringing" else { return }
                    self.stop(reason: .unanswered)
                }
                self.incomingDeadline = deadline; DispatchQueue.main.asyncAfter(deadline: .now() + 120, execute: deadline)
                Task { @MainActor in
                    do {
                        try await self.validateOffer(captured, delivery: identifier)
                        guard self.generation == expected, self.callUUID == identifierUUID, self.phase == "ringing",
                              self.credential()?.deviceID == captured.deviceID else { return }
                        self.lastAuthorized = ProcessInfo.processInfo.systemUptime
                        // A valid offer still does not admit, connect or capture.
                    } catch { if self.generation == expected && self.callUUID == identifierUUID && self.phase == "ringing" { self.stop(reason: .failed) } }
                }
            }
        }
    }
    func start(roomID: String, completion: @escaping (Bool) -> Void) {
        guard Self.validID(roomID) != nil, credential() != nil, phase == "idle", !stoppingMedia,
              AVAudioSession.sharedInstance().recordPermission == .granted else { completion(false); return }
        let uuid = UUID(); callUUID = uuid; outgoingRoomID = roomID; phase = "connecting"
        let action = CXStartCallAction(call: uuid, handle: CXHandle(type: .generic, value: "FreeCollab call")); action.isVideo = false
        let expected = generation
        controller.request(CXTransaction(action: action)) { [weak self] error in
            DispatchQueue.main.async {
                if let self = self, error != nil, self.generation == expected, self.callUUID == uuid { self.stop(reason: .failed) }
                completion(error == nil)
            }
        }
    }
    func answer(completion: @escaping (Bool) -> Void) {
        guard let uuid = callUUID, deliveryID != nil, phase == "ringing", credential() != nil else { completion(false); return }
        let expected = generation
        controller.request(CXTransaction(action: CXAnswerCallAction(call: uuid))) { [weak self] error in
            DispatchQueue.main.async {
                if let self = self, error != nil, self.generation == expected, self.callUUID == uuid { self.stop(reason: .failed) }
                completion(error == nil)
            }
        }
    }
    func mute(_ value: Bool, completion: @escaping (Bool) -> Void) {
        guard let uuid = callUUID, sessionID != nil else { completion(false); return }
        // Mute must take effect even if the backend is temporarily unreachable.
        if value { AudioManager.shared.isMicrophoneMuted = true }
        controller.request(CXTransaction(action: CXSetMutedCallAction(call: uuid, muted: value))) { error in completion(error == nil) }
    }
    func end() {
        guard let uuid = callUUID else { stop(reason: .remoteEnded); return }
        // End capture immediately; OS transaction completes the visible call.
        stop(reason: .remoteEnded, report: false)
        controller.request(CXTransaction(action: CXEndCallAction(call: uuid))) { [weak self] error in
            if error != nil { self?.provider.reportCall(with: uuid, endedAt: Date(), reason: .remoteEnded) }
        }
    }
    func providerDidReset(_ provider: CXProvider) { stop(reason: .failed, report: false) }
    func provider(_ provider: CXProvider, timedOutPerforming action: CXAction) {
        action.fail()
        if let call = action as? CXCallAction, FreeCollabCallingPolicy.actionOwnsCall(action: call.callUUID, current: callUUID) { stop(reason: .failed) }
    }
    func provider(_ provider: CXProvider, perform action: CXAnswerCallAction) {
        guard action.callUUID == callUUID, let delivery = deliveryID, phase == "ringing" else { action.fail(); return }
        admit(action: action, path: "offers/" + delivery + "/answer", body: ["explicit_answer": true, "client_call_id": delivery])
    }
    func provider(_ provider: CXProvider, perform action: CXStartCallAction) {
        guard action.callUUID == callUUID, let room = outgoingRoomID else { action.fail(); return }
        provider.reportOutgoingCall(with: action.callUUID, startedConnectingAt: Date())
        admit(action: action, path: "join", body: ["explicit_answer": true, "client_call_id": action.callUUID.uuidString.replacingOccurrences(of: "-", with: "").lowercased(), "room_id": room])
    }
    private func admit(action: CXAction, path: String, body: [String: Any]) {
        guard let captured = credential(), AVAudioSession.sharedInstance().recordPermission == .granted else { action.fail(); stop(reason: .failed); return }
        phase = "connecting"; incomingDeadline?.cancel(); pendingAction = action
        let expected = generation
        Task { @MainActor in
            do {
                let access = try await FreeCollabCallHTTP.request(captured, path, body: body)
                guard self.generation == expected, self.credential()?.deviceID == captured.deviceID else {
                    if let id = access["session_id"] as? String { _ = try? await FreeCollabCallHTTP.request(captured, "sessions/" + id + "/end") }
                    action.fail(); return
                }
                guard let id = Self.validID(access["session_id"] as? String), let address = access["url"] as? String,
                      let mediaURL = URL(string: address), mediaURL.scheme == "wss", mediaURL.host != nil,
                      mediaURL.user == nil, mediaURL.password == nil, mediaURL.query == nil, mediaURL.fragment == nil,
                      mediaURL.path.isEmpty || mediaURL.path == "/", let token = access["token"] as? String,
                      token.count <= 8192, access["audio_e2ee"] as? Bool == false else { throw FreeCollabCallFailure.unavailable }
                self.sessionID = id; self.explicitAnswer = true; self.lastAuthorized = ProcessInfo.processInfo.systemUptime
                try AVAudioSession.sharedInstance().setCategory(.playAndRecord, mode: .voiceChat, options: [.allowBluetooth])
                AudioManager.shared.audioSession.isAutomaticConfigurationEnabled = false
                try AudioManager.shared.setEngineAvailability(.none)
                let room = Room(delegate: self); self.room = room
                self.startTimers(expected: expected)
                // Fulfilling lets CallKit activate audio; the engine remains
                // blocked until didActivate AND post-connect authorization.
                action.fulfill(); self.pendingAction = nil
                try await room.connect(url: address, token: token, connectOptions: ConnectOptions(autoSubscribe: true,
                    reconnectAttempts: 2, socketConnectTimeoutInterval: 5, primaryTransportConnectTimeout: 5,
                    publisherTransportConnectTimeout: 5, enableMicrophone: false))
                guard self.generation == expected, self.room === room else { await room.disconnect(); return }
                self.mediaReady = true
                try await self.activateMedia(expected: expected)
                guard self.generation == expected else { return }
                self.phase = "active"
                if let uuid = self.callUUID, self.outgoingRoomID != nil { self.provider.reportOutgoingCall(with: uuid, connectedAt: Date()) }
            } catch { if self.generation == expected { self.pendingAction?.fail(); self.pendingAction = nil; self.stop(reason: .failed) } }
        }
    }
    func provider(_ provider: CXProvider, perform action: CXEndCallAction) {
        // A delayed OS end from an older generation cannot end a newer call.
        if FreeCollabCallingPolicy.actionOwnsCall(action: action.callUUID, current: callUUID) { stop(reason: .remoteEnded, report: false) }
        action.fulfill()
    }
    func provider(_ provider: CXProvider, perform action: CXSetMutedCallAction) {
        guard action.callUUID == callUUID else { action.fail(); return }
        control(muted: action.isMuted, held: held, action: action)
    }
    func provider(_ provider: CXProvider, perform action: CXSetHeldCallAction) {
        guard action.callUUID == callUUID else { action.fail(); return }
        control(muted: muted, held: action.isOnHold, action: action)
    }
    private func control(muted nextMuted: Bool, held nextHeld: Bool, action: CXAction) {
        guard let captured = credential(), let id = sessionID else { action.fail(); return }
        muted = nextMuted; held = nextHeld; controlGeneration += 1
        let expected = generation, control = controlGeneration
        AudioManager.shared.isMicrophoneMuted = true
        try? AudioManager.shared.setEngineAvailability(AudioEngineAvailability(isInputAvailable: false, isOutputAvailable: audioActivated && !nextHeld))
        Task { @MainActor in
            do {
                try await self.authorize(captured, id: id, muted: nextMuted, held: nextHeld)
                guard self.generation == expected, self.controlGeneration == control else { action.fail(); return }
                self.lastAuthorized = ProcessInfo.processInfo.systemUptime
                try await self.activateMedia(expected: expected, authorizedControl: control)
                guard self.generation == expected, self.controlGeneration == control else { action.fail(); return }
                action.fulfill()
            } catch { action.fail(); if self.generation == expected { self.stop(reason: .failed) } }
        }
    }
    func provider(_ provider: CXProvider, didActivate audioSession: AVAudioSession) {
        audioActivated = true; let expected = generation
        Task { @MainActor in do { try await self.activateMedia(expected: expected) } catch { if self.generation == expected { self.stop(reason: .failed) } } }
    }
    func provider(_ provider: CXProvider, didDeactivate audioSession: AVAudioSession) {
        audioActivated = false; AudioManager.shared.isMicrophoneMuted = true
        try? AudioManager.shared.setEngineAvailability(.none)
    }
    private func activateMedia(expected: Int, authorizedControl: Int? = nil) async throws {
        guard FreeCollabCallingPolicy.mediaAllowed(generation: generation, expected: expected, explicitAnswer: explicitAnswer, audioActivated: audioActivated, mediaReady: mediaReady), let room = room,
              let captured = credential(), let id = sessionID else { return }
        let control = controlGeneration
        // OS mute/hold already fetched fresh authorization for this exact
        // control generation; activation/reconnection still fetches its own.
        if authorizedControl != control { try await authorize(captured, id: id, muted: muted, held: held) }
        guard generation == expected, controlGeneration == control, audioActivated, self.room === room else { return }
        lastAuthorized = ProcessInfo.processInfo.systemUptime
        try AudioManager.shared.setEngineAvailability(AudioEngineAvailability(isInputAvailable: !muted && !held, isOutputAvailable: !held))
        try await room.localParticipant.setMicrophone(enabled: !muted && !held)
        guard generation == expected else {
            // Replacement calls stay blocked until old SDK teardown completes.
            AudioManager.shared.isMicrophoneMuted = true; try? AudioManager.shared.setEngineAvailability(.none); return
        }
        guard controlGeneration == control, audioActivated else {
            // A newer control owns its gate. Never let an older SDK completion
            // reopen input after the latest mute/hold/deactivation intent.
            if muted || held || !audioActivated {
                AudioManager.shared.isMicrophoneMuted = true
                try? AudioManager.shared.setEngineAvailability(AudioEngineAvailability(isInputAvailable: false, isOutputAvailable: audioActivated && !held))
            }
            return
        }
        AudioManager.shared.isMicrophoneMuted = muted || held
    }
    private func authorize(_ captured: FreeCollabCallCredential, id: String, muted: Bool, held: Bool) async throws {
        let response = try await FreeCollabCallHTTP.request(captured, "sessions/" + id + "/heartbeat", body: ["muted": muted, "held": held])
        guard response["active"] as? Bool == true else { throw FreeCollabCallFailure.unavailable }
    }
    private func validateOffer(_ captured: FreeCollabCallCredential, delivery: String) async throws {
        let offer = try await FreeCollabCallHTTP.request(captured, "offers/" + delivery, method: "GET", body: nil)
        guard offer["call_id"] as? String == delivery, Self.validID(offer["room_id"] as? String) != nil,
              offer["audio_e2ee"] as? Bool == false else { throw FreeCollabCallFailure.unavailable }
    }
    private func startRingingTimers(expected: Int, captured: FreeCollabCallCredential, delivery: String) {
        heartbeatTimer?.cancel(); watchdogTimer?.cancel()
        lastAuthorized = ProcessInfo.processInfo.systemUptime
        let monitor = DispatchSource.makeTimerSource(queue: .main)
        monitor.schedule(deadline: .now() + 5, repeating: 5)
        monitor.setEventHandler { [weak self] in
            guard let self = self, self.generation == expected, self.phase == "ringing", !self.heartbeatBusy else { return }
            self.heartbeatBusy = true
            Task { @MainActor in
                defer { if self.generation == expected { self.heartbeatBusy = false } }
                do {
                    try await self.validateOffer(captured, delivery: delivery)
                    if self.generation == expected && self.phase == "ringing" { self.lastAuthorized = ProcessInfo.processInfo.systemUptime }
                } catch { if self.generation == expected && self.phase == "ringing" { self.stop(reason: .failed) } }
            }
        }
        let watchdog = DispatchSource.makeTimerSource(queue: .main)
        watchdog.schedule(deadline: .now() + 1, repeating: 1)
        watchdog.setEventHandler { [weak self] in
            guard let self = self, self.generation == expected else { return }
            if self.credential()?.deviceID != captured.deviceID || FreeCollabCallingPolicy.authorizationExpired(lastSuccessful: self.lastAuthorized, now: ProcessInfo.processInfo.systemUptime) { self.stop(reason: .failed) }
        }
        heartbeatTimer = monitor; watchdogTimer = watchdog; monitor.resume(); watchdog.resume()
    }
    private func startTimers(expected: Int) {
        heartbeatTimer?.cancel(); watchdogTimer?.cancel()
        let heartbeat = DispatchSource.makeTimerSource(queue: .main)
        heartbeat.schedule(deadline: .now() + 5, repeating: 5)
        heartbeat.setEventHandler { [weak self] in
            guard let self = self, self.generation == expected, !self.heartbeatBusy,
                  let captured = self.credential(), let id = self.sessionID else { return }
            self.heartbeatBusy = true
            Task { @MainActor in
                defer { if self.generation == expected { self.heartbeatBusy = false } }
                do {
                    try await self.authorize(captured, id: id, muted: self.muted, held: self.held)
                    if self.generation == expected { self.lastAuthorized = ProcessInfo.processInfo.systemUptime }
                } catch { if self.generation == expected { self.stop(reason: .failed) } }
            }
        }
        let watchdog = DispatchSource.makeTimerSource(queue: .main)
        watchdog.schedule(deadline: .now() + 1, repeating: 1)
        watchdog.setEventHandler { [weak self] in
            guard let self = self, self.generation == expected else { return }
            if self.credential() == nil || FreeCollabCallingPolicy.authorizationExpired(lastSuccessful: self.lastAuthorized, now: ProcessInfo.processInfo.systemUptime) { self.stop(reason: .failed) }
        }
        heartbeatTimer = heartbeat; watchdogTimer = watchdog; heartbeat.resume(); watchdog.resume()
    }
    func stop(reason: CXCallEndedReason, report: Bool = true) {
        let captured = credential(), oldSession = sessionID, oldRoom = room, oldUUID = callUUID
        generation += 1; controlGeneration += 1; heartbeatTimer?.cancel(); watchdogTimer?.cancel(); incomingDeadline?.cancel()
        heartbeatTimer = nil; watchdogTimer = nil; incomingDeadline = nil; heartbeatBusy = false
        pendingAction?.fail(); pendingAction = nil
        AudioManager.shared.isMicrophoneMuted = true; try? AudioManager.shared.setEngineAvailability(.none)
        room = nil; sessionID = nil; callUUID = nil; deliveryID = nil; outgoingRoomID = nil
        phase = "idle"; muted = false; held = false; audioActivated = false; mediaReady = false; explicitAnswer = false
        if report, let uuid = oldUUID { provider.reportCall(with: uuid, endedAt: Date(), reason: reason) }
        if let oldRoom = oldRoom {
            stoppingMedia = true; let teardown = UUID(); teardownID = teardown
            Task { @MainActor in
                // SDK publish operations serialize internally. Keep the global
                // engine gate closed and reject replacement calls until any
                // in-flight enable is followed by mute/disconnect completion.
                _ = try? await oldRoom.localParticipant.setMicrophone(enabled: false)
                await oldRoom.disconnect()
                if self.teardownID == teardown { self.teardownID = nil; self.stoppingMedia = false }
            }
        }
        if let captured = captured, let id = oldSession { Task { _ = try? await FreeCollabCallHTTP.request(captured, "sessions/" + id + "/end") } }
    }
    func room(_ room: Room, didDisconnectWithError error: LiveKitError?) {
        DispatchQueue.main.async { if self.room === room { self.stop(reason: .remoteEnded) } }
    }
    func room(_ room: Room, didStartReconnectWithMode reconnectMode: ReconnectMode) {
        DispatchQueue.main.async { if self.room === room { self.mediaReady = false; AudioManager.shared.isMicrophoneMuted = true; try? AudioManager.shared.setEngineAvailability(.none) } }
    }
    func room(_ room: Room, didCompleteReconnectWithMode reconnectMode: ReconnectMode) {
        DispatchQueue.main.async {
            guard self.room === room else { return }; self.mediaReady = true; let expected = self.generation
            Task { @MainActor in do { try await self.activateMedia(expected: expected) } catch { if self.generation == expected { self.stop(reason: .failed) } } }
        }
    }
}

/// The frame guard in SceneDelegate remains the actual origin/main-frame edge.
@objc(FreeCollabCallingPlugin)
public class FreeCollabCallingPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "FreeCollabCallingPlugin"
    public let jsName = "FreeCollabCalling"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "prepareCalling", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "configureCalling", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "getCallState", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "joinCall", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "answerCall", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "muteCall", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "endCall", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "disableCalling", returnType: CAPPluginReturnPromise)
    ]
    private func installation() -> String? {
        guard let origin = FreeCollabCallManager.origin(bridge?.config.serverURL.absoluteString),
              origin == FreeCollabCallManager.origin(webView?.url?.absoluteString),
              UIApplication.shared.applicationState == .active else { return nil }; return origin
    }
    @objc func prepareCalling(_ call: CAPPluginCall) {
        DispatchQueue.main.async {
            guard self.installation() != nil else { call.reject("The installation changed."); return }
            AVAudioSession.sharedInstance().requestRecordPermission { granted in
                DispatchQueue.main.async { if granted && self.installation() != nil { call.resolve() } else { call.reject("Allow microphone access in device settings before enabling native calls.") } }
            }
        }
    }
    @objc func configureCalling(_ call: CAPPluginCall) {
        DispatchQueue.main.async {
            guard let origin = self.installation() else { call.reject("The installation changed."); return }
            do { try FreeCollabCallManager.shared.configure(origin: origin, deviceID: call.getString("device_id") ?? "", secret: call.getString("call_secret") ?? ""); call.resolve() }
            catch { call.reject("Native calling could not be configured.") }
        }
    }
    @objc func getCallState(_ call: CAPPluginCall) {
        DispatchQueue.main.async { guard self.installation() != nil else { call.reject("The installation changed."); return }; call.resolve(FreeCollabCallManager.shared.status()) }
    }
    @objc func joinCall(_ call: CAPPluginCall) {
        DispatchQueue.main.async {
            guard self.installation() != nil, call.getBool("explicit_answer") == true else { call.reject("Explicit call consent is required."); return }
            FreeCollabCallManager.shared.start(roomID: call.getString("room_id") ?? "") { success in if success { call.resolve() } else { call.reject("Native call could not be started.") } }
        }
    }
    @objc func answerCall(_ call: CAPPluginCall) {
        DispatchQueue.main.async {
            guard self.installation() != nil, call.getBool("explicit_answer") == true else { call.reject("Explicit call consent is required."); return }
            FreeCollabCallManager.shared.answer { success in if success { call.resolve() } else { call.reject("Native call could not be answered.") } }
        }
    }
    @objc func muteCall(_ call: CAPPluginCall) {
        DispatchQueue.main.async {
            guard self.installation() != nil else { call.reject("The installation changed."); return }
            FreeCollabCallManager.shared.mute(call.getBool("muted") == true) { success in if success { call.resolve() } else { call.reject("Native call controls are unavailable.") } }
        }
    }
    @objc func endCall(_ call: CAPPluginCall) {
        DispatchQueue.main.async { guard self.installation() != nil else { call.reject("The installation changed."); return }; FreeCollabCallManager.shared.end(); call.resolve() }
    }
    @objc func disableCalling(_ call: CAPPluginCall) {
        DispatchQueue.main.async { guard self.installation() != nil else { call.reject("The installation changed."); return }; FreeCollabCallManager.shared.disable(); call.resolve() }
    }
}
