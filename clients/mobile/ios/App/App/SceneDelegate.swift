import UIKit
import WebKit
import Capacitor
import CryptoKit
import Security

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
    override func capacitorDidLoad() { bridge?.registerPluginInstance(FreeCollabNativePlugin()) }
}

/// System-browser login; only one-use PKCE material crosses the callback URL.
@objc(FreeCollabNativePlugin)
public class FreeCollabNativePlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "FreeCollabNativePlugin"
    public let jsName = "FreeCollabNative"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "signIn", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "cancelSignIn", returnType: CAPPluginReturnPromise)
    ]
    private var pending: CAPPluginCall?
    private var state: String?
    private var verifier: String?
    private var deadline: Date?
    private var observer: NSObjectProtocol?
    private let callback = "freecollab://native/complete"

    public override func load() {
        observer = NotificationCenter.default.addObserver(forName: .capacitorOpenURL, object: nil, queue: .main) { [weak self] notification in
            guard let values = notification.object as? [String: Any], let url = values["url"] as? URL else { return }
            self?.complete(url)
        }
    }
    deinit { if let observer = observer { NotificationCenter.default.removeObserver(observer) } }

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

