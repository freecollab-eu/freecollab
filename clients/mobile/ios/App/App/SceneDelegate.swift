import UIKit
import WebKit
import Capacitor
import CryptoKit
import Security
import UserNotifications

class SceneDelegate: UIResponder, UIWindowSceneDelegate {
    var window: UIWindow?
    func scene(_ scene: UIScene, willConnectTo session: UISceneSession, options connectionOptions: UIScene.ConnectionOptions) {
        guard let windowScene = scene as? UIWindowScene else { return }
        window = UIWindow(windowScene: windowScene)
        window?.rootViewController = FreeCollabBridgeViewController()
        window?.makeKeyAndVisible()
        SceneDelegateProxy.shared.scene(scene, willConnectTo: session, options: connectionOptions)
    }
    func scene(_ scene: UIScene, openURLContexts URLContexts: Set<UIOpenURLContext>) {
        SceneDelegateProxy.shared.scene(scene, openURLContexts: URLContexts)
    }
    func scene(_ scene: UIScene, continue userActivity: NSUserActivity) {
        SceneDelegateProxy.shared.scene(scene, continue: userActivity)
    }
}

class FreeCollabBridgeViewController: CAPBridgeViewController {
    override func capacitorDidLoad() {
        bridge?.registerPluginInstance(FreeCollabNativePlugin())
        bridge?.registerPluginInstance(FreeCollabPushPlugin())
        bridge?.registerPluginInstance(FreeCollabCallingPlugin())
        if let runtime = bridge as? CapacitorBridge, let view = webView {
            // Capacitor's JS export is main-frame-only, but an iframe can still
            // address a WK message handler directly. Guard the actual native edge.
            let controller = view.configuration.userContentController
            controller.removeScriptMessageHandler(forName: "bridge")
            controller.add(FreeCollabFrameGuard(delegate: runtime.webViewDelegationHandler, webView: view,
                configured: runtime.config.serverURL), name: "bridge")
        }
    }
}

private final class FreeCollabFrameGuard: NSObject, WKScriptMessageHandler {
    private let delegate: WebViewDelegationHandler
    private weak var webView: WKWebView?
    private let configured: URL?
    init(delegate: WebViewDelegationHandler, webView: WKWebView, configured: URL?) {
        self.delegate = delegate; self.webView = webView; self.configured = configured
    }
    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame, let own = configured, own.scheme == "https", own.user == nil, own.password == nil,
              let current = webView?.url, current.scheme == "https", current.host == own.host,
              (current.port ?? 443) == (own.port ?? 443) else { return }
        let origin = message.frameInfo.securityOrigin
        guard origin.protocol == "https", origin.host == own.host,
              (origin.port == 0 ? 443 : origin.port) == (own.port ?? 443) else { return }
        delegate.userContentController(controller, didReceive: message)
    }
}

/// System-browser login; only one-use PKCE material crosses the callback URL.
@objc(FreeCollabNativePlugin)
public class FreeCollabNativePlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "FreeCollabNativePlugin"
    public let jsName = "FreeCollabNative"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "signIn", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "cancelSignIn", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "getPendingLink", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "clearPendingLink", returnType: CAPPluginReturnPromise)
    ]
    private var pending: CAPPluginCall?
    private var state: String?
    private var verifier: String?
    private var deadline: Date?
    private var observer: NSObjectProtocol?
    private var linkObserver: NSObjectProtocol?
    private var pendingLink: String?
    private var pendingLinkGeneration: String?
    private static var consumedLink: String?
    private let callback = "freecollab://native/complete"

    public override func load() {
        acceptLink(ApplicationDelegateProxy.shared.lastURL, restoring: true)
        observer = NotificationCenter.default.addObserver(forName: .capacitorOpenURL, object: nil, queue: .main) { [weak self] notification in
            guard let values = notification.object as? [String: Any], let url = values["url"] as? URL else { return }
            self?.complete(url)
        }
        linkObserver = NotificationCenter.default.addObserver(forName: .capacitorOpenUniversalLink, object: nil, queue: .main) { [weak self] notification in
            guard let values = notification.object as? [String: Any], let url = values["url"] as? URL else { return }
            self?.acceptLink(url)
        }
    }
    deinit {
        if let observer = observer { NotificationCenter.default.removeObserver(observer) }
        if let linkObserver = linkObserver { NotificationCenter.default.removeObserver(linkObserver) }
    }

    private func acceptLink(_ url: URL?, restoring: Bool = false) {
        guard let url = url, let configured = origin(bridge?.config.serverURL), origin(url) == configured, url.path == "/open",
              url.fragment == nil, url.absoluteString.count <= 2048,
              let parts = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return }
        if restoring && Self.consumedLink == url.absoluteString { return }
        let items = parts.queryItems ?? [], keys = items.map { $0.name }
        let allowed: Set<String> = ["workspace_id", "channel_id", "kind", "item_id", "project_id"]
        guard Set(keys).count == keys.count, Set(keys).isSubset(of: allowed),
              Set(["workspace_id", "channel_id", "kind", "item_id"]).isSubset(of: Set(keys)) else { return }
        pendingLink = url.absoluteString
        pendingLinkGeneration = UUID().uuidString
    }
    @objc func getPendingLink(_ call: CAPPluginCall) {
        guard installation() != nil else { call.reject("The installation changed."); return }
        call.resolve(pendingLink.map { ["url": $0, "generation": pendingLinkGeneration ?? ""] } ?? [:])
    }
    @objc func clearPendingLink(_ call: CAPPluginCall) {
        guard installation() != nil else { call.reject("The installation changed."); return }
        if let value = call.getString("url"), pendingLink == value, pendingLinkGeneration == call.getString("generation") {
            pendingLink = nil
            pendingLinkGeneration = nil
            Self.consumedLink = value
        }
        call.resolve()
    }

    private func origin(_ url: URL?) -> String? {
        guard let url = url, url.scheme == "https", let host = url.host, url.user == nil, url.password == nil else { return nil }
        return "https://" + host + (url.port.map { $0 == 443 ? "" : ":\($0)" } ?? "")
    }
    private func installation() -> String? {
        let configured = origin(bridge?.config.serverURL)
        return configured != nil && configured == origin(webView?.url) ? configured : nil
    }
    private func encoded(_ value: Data) -> String {
        return value.base64EncodedString().replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
    }
    private func random() throws -> String {
        var bytes = [UInt8](repeating: 0, count: 32)
        guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else {
            throw NSError(domain: "FreeCollab", code: 1)
        }
        return encoded(Data(bytes))
    }
    private func clear() { pending = nil; state = nil; verifier = nil; deadline = nil }

    @objc func signIn(_ call: CAPPluginCall) {
        DispatchQueue.main.async { [weak self] in
            guard let self = self, let address = self.installation(), self.pending == nil else {
                call.reject("A browser sign-in is unavailable or already open."); return
            }
            do {
                let verifier = try self.random(), state = try self.random()
                let deadline = Date().addingTimeInterval(600)
                let context: [String: Any] = [
                    "protocol": "freecollab-native-v1", "clientKind": "mobile", "redirectUri": self.callback,
                    "state": state, "codeChallenge": self.encoded(Data(SHA256.hash(data: Data(verifier.utf8)))),
                    "expiresAt": deadline.timeIntervalSince1970 * 1000
                ]
                let encoded = self.encoded(try JSONSerialization.data(withJSONObject: context))
                guard let url = URL(string: address + "/#native-login=" + encoded) else { throw NSError(domain: "FreeCollab", code: 2) }
                self.pending = call; self.state = state; self.verifier = verifier; self.deadline = deadline
                UIApplication.shared.open(url, options: [:]) { [weak self] opened in
                    if !opened { self?.pending?.reject("The system browser could not be opened."); self?.clear() }
                }
                DispatchQueue.main.asyncAfter(deadline: .now() + 600) { [weak self] in
                    guard let self = self, self.state == state else { return }
                    self.pending?.reject("Browser sign-in timed out. Start again."); self.clear()
                }
            } catch { self.clear(); call.reject("The system browser could not be opened.") }
        }
    }
    @objc func cancelSignIn(_ call: CAPPluginCall) {
        DispatchQueue.main.async { [weak self] in
            self?.pending?.reject("Browser sign-in cancelled."); self?.clear(); call.resolve()
        }
    }
    private func complete(_ url: URL) {
        guard let call = pending, url.scheme == "freecollab", url.host == "native", url.path == "/complete",
              url.fragment == nil, let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
              parts.queryItems?.first(where: { $0.name == "state" })?.value == state else { return }
        guard let deadline = deadline, Date() < deadline, installation() != nil, let verifier = verifier,
              let code = parts.queryItems?.first(where: { $0.name == "code" })?.value,
              code.range(of: "^[A-Za-z0-9_-]{43}$", options: .regularExpression) != nil else {
            call.reject("Browser sign-in is invalid or expired."); clear(); return
        }
        clear(); call.resolve(["code": code, "code_verifier": verifier, "redirect_uri": callback])
    }
    public override func shouldOverrideLoad(_ action: WKNavigationAction) -> NSNumber? {
        guard let url = action.request.url else { return NSNumber(value: true) }
        if let own = origin(bridge?.config.serverURL), own == origin(url) { return NSNumber(value: false) }
        if let external = origin(url) {
            DispatchQueue.main.async { [weak self] in
                let alert = UIAlertController(title: "Open in your browser?", message: external, preferredStyle: .alert)
                alert.addAction(UIAlertAction(title: "Cancel", style: .cancel))
                alert.addAction(UIAlertAction(title: "Open browser", style: .default) { _ in UIApplication.shared.open(url) })
                self?.bridge?.viewController?.present(alert, animated: true)
            }
        }
        return NSNumber(value: true)
    }
}



extension Notification.Name {
    static let freecollabAPNsRegistration = Notification.Name("FreeCollabAPNsRegistration")
}

/// Only opaque, validated routing and one-use device proofs persist natively.
enum FreeCollabPushStore {
    static let key = "freecollab-native-push-pending"
    static func valid(_ value: Any?, _ length: Int) -> String? {
        guard let text = value as? String, text.range(of: "^[A-Za-z0-9_-]{\(length)}$", options: .regularExpression) != nil else { return nil }
        return text
    }
    static func items() -> [[String: String]] { return UserDefaults.standard.array(forKey: key) as? [[String: String]] ?? [] }
    static func store(_ values: [AnyHashable: Any]) {
        guard let type = values["type"] as? String else { return }
        var item: [String: String] = ["type": type]
        if type == "subscription_confirmation" {
            guard let device = valid(values["device_id"], 32), let challenge = valid(values["challenge"], 43) else { return }
            item["key"] = device; item["device_id"] = device; item["challenge"] = challenge
        } else if type == "activity" || type == "incoming_call" {
            guard let identifier = valid(values["id"], 32) else { return }
            item["key"] = identifier; item["id"] = identifier
        } else { return }
        var pending = items().filter { $0["key"] != item["key"] }
        pending = Array(pending.suffix(9)); pending.append(item)
        UserDefaults.standard.set(pending, forKey: key)
    }
    static func clear(_ identifier: String) {
        UserDefaults.standard.set(items().filter { $0["key"] != identifier }, forKey: key)
    }
}

/// Registration only follows explicit device consent from the configured origin.
@objc(FreeCollabPushPlugin)
public class FreeCollabPushPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "FreeCollabPushPlugin"
    public let jsName = "FreeCollabPush"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "registerPush", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "refreshPush", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "unregisterPush", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "getPendingPush", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "clearPush", returnType: CAPPluginReturnPromise)
    ]
    private var pending: CAPPluginCall?
    private var observer: NSObjectProtocol?
    private func origin(_ url: URL?) -> String? {
        guard let url = url, url.scheme == "https", let host = url.host, url.user == nil, url.password == nil else { return nil }
        return "https://" + host + (url.port.map { $0 == 443 ? "" : ":\($0)" } ?? "")
    }
    private func installation() -> Bool {
        guard let configured = origin(bridge?.config.serverURL) else { return false }
        return configured == origin(webView?.url)
    }
    public override func load() {
        observer = NotificationCenter.default.addObserver(forName: .freecollabAPNsRegistration, object: nil, queue: .main) { [weak self] notification in
            guard let self = self, let call = self.pending else { return }
            self.pending = nil
            guard self.installation(), let token = notification.object as? String else {
                call.reject("Native registration could not be completed."); return
            }
            call.resolve(["platform": "ios", "token": token])
        }
    }
    deinit { if let observer = observer { NotificationCenter.default.removeObserver(observer) } }
    @objc func registerPush(_ call: CAPPluginCall) {
        DispatchQueue.main.async { [weak self] in
            guard let self = self, self.installation(), self.pending == nil else { call.reject("Native notifications are unavailable or already opening."); return }
            self.pending = call
            UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { [weak self] granted, error in
                DispatchQueue.main.async {
                    guard let self = self, self.pending === call else { return }
                    guard granted, error == nil, self.installation() else {
                        self.pending = nil; call.reject("Notifications are disabled or the installation changed."); return
                    }
                    UIApplication.shared.registerForRemoteNotifications()
                }
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 30) { [weak self] in
                guard let self = self, self.pending === call else { return }
                self.pending = nil; call.reject("Native registration timed out. Check signing and the push capability.")
            }
        }
    }
    @objc func refreshPush(_ call: CAPPluginCall) {
        UNUserNotificationCenter.current().getNotificationSettings { [weak self] settings in
            DispatchQueue.main.async {
                guard let self = self, self.installation(), self.pending == nil, settings.authorizationStatus == .authorized else {
                    call.reject("Notifications require explicit device consent."); return
                }
                self.pending = call
                UIApplication.shared.registerForRemoteNotifications()
                DispatchQueue.main.asyncAfter(deadline: .now() + 30) { [weak self] in
                    guard let self = self, self.pending === call else { return }
                    self.pending = nil; call.reject("Native registration timed out.")
                }
            }
        }
    }
    @objc func unregisterPush(_ call: CAPPluginCall) {
        DispatchQueue.main.async { [weak self] in
            guard let self = self, self.installation() else { call.reject("The installation changed."); return }
            self.pending?.reject("Native registration cancelled."); self.pending = nil
            FreeCollabCallManager.shared.disable()
            UIApplication.shared.unregisterForRemoteNotifications()
            UNUserNotificationCenter.current().removeAllDeliveredNotifications()
            UserDefaults.standard.removeObject(forKey: FreeCollabPushStore.key); call.resolve()
        }
    }
    @objc func getPendingPush(_ call: CAPPluginCall) {
        guard installation() else { call.reject("The installation changed."); return }
        call.resolve(["items": FreeCollabPushStore.items()])
    }
    @objc func clearPush(_ call: CAPPluginCall) {
        guard installation() else { call.reject("The installation changed."); return }
        FreeCollabPushStore.clear(call.getString("key") ?? ""); call.resolve()
    }
}
