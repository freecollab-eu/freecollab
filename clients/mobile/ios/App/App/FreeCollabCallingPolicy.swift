import Foundation

/// Platform-independent admission rules used at the native callback boundary.
/// The CLI test executes this same file without replacing CallKit/LiveKit APIs.
enum FreeCollabCallingPolicy {
    static func origin(_ value: String?) -> String? {
        guard let value = value, value.count <= 2048, !value.contains(where: { $0.isWhitespace }),
              let url = URL(string: value), url.scheme == "https", let host = url.host, !host.isEmpty,
              url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
              url.path.isEmpty || url.path == "/" else { return nil }
        return "https://" + host.lowercased() + (url.port.map { $0 == 443 ? "" : ":\($0)" } ?? "")
    }
    static func apiURL(origin value: String, path: String) -> URL? {
        guard origin(value) == value, !path.isEmpty, !path.hasPrefix("/"),
              path.range(of: "^[a-zA-Z0-9/_-]+$", options: .regularExpression) != nil else { return nil }
        return URL(string: value + "/api/mobile/native/calling/" + path)
    }
    static func identifier(_ value: String?) -> String? {
        guard let value = value, value.range(of: "^[a-f0-9]{32}$", options: .regularExpression) != nil else { return nil }; return value
    }
    static func mediaAllowed(generation: Int, expected: Int, explicitAnswer: Bool, audioActivated: Bool, mediaReady: Bool) -> Bool {
        generation == expected && explicitAnswer && audioActivated && mediaReady
    }
    static func authorizationExpired(lastSuccessful: TimeInterval, now: TimeInterval) -> Bool {
        !lastSuccessful.isFinite || !now.isFinite || now < lastSuccessful || now - lastSuccessful >= 15
    }
    static func actionOwnsCall(action: UUID, current: UUID?) -> Bool { action == current }
}
