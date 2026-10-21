package eu.freecollab.mobile;

import android.content.Intent;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.util.Base64;
import androidx.appcompat.app.AlertDialog;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;

/** The verifier never enters a browser URL or persistent preferences. */
@CapacitorPlugin(name = "FreeCollabNative")
public class FreeCollabNativePlugin extends Plugin {
    private PluginCall pending;
    private String state;
    private String verifier;
    private long deadline;
    private final Handler timer = new Handler(Looper.getMainLooper());
    private Runnable timeout;
    private static final String CALLBACK = "freecollab://native/complete";

    private String origin(Uri uri) {
        if (uri == null || !"https".equals(uri.getScheme()) || uri.getEncodedAuthority() == null
            || uri.getEncodedAuthority().contains("@")) return null;
        return "https://" + uri.getEncodedAuthority();
    }

    private String installation() {
        String configured = origin(Uri.parse(getBridge().getConfig().getServerUrl()));
        String current = origin(Uri.parse(getBridge().getWebView().getUrl()));
        return configured != null && configured.equals(current) ? configured : null;
    }

    private String random() {
        byte[] bytes = new byte[32]; new SecureRandom().nextBytes(bytes);
        return Base64.encodeToString(bytes, Base64.URL_SAFE | Base64.NO_WRAP | Base64.NO_PADDING);
    }

    private void clear() {
        if (timeout != null) timer.removeCallbacks(timeout);
        pending = null; state = null; verifier = null; deadline = 0;
    }

    @PluginMethod
    public void signIn(PluginCall call) {
        getActivity().runOnUiThread(() -> {
            String address = installation();
            if (address == null || pending != null) { call.reject("A browser sign-in is unavailable or already open."); return; }
            try {
                verifier = random(); state = random(); deadline = System.currentTimeMillis() + 600000;
                String challenge = Base64.encodeToString(MessageDigest.getInstance("SHA-256").digest(verifier.getBytes(StandardCharsets.US_ASCII)),
                    Base64.URL_SAFE | Base64.NO_WRAP | Base64.NO_PADDING);
                JSObject context = new JSObject();
                context.put("protocol", "freecollab-native-v1"); context.put("clientKind", "mobile");
                context.put("redirectUri", CALLBACK); context.put("state", state);
                context.put("codeChallenge", challenge); context.put("expiresAt", deadline);
                String encoded = Base64.encodeToString(context.toString().getBytes(StandardCharsets.UTF_8),
                    Base64.URL_SAFE | Base64.NO_WRAP | Base64.NO_PADDING);
                pending = call;
                timeout = () -> { if (pending != null) pending.reject("Browser sign-in timed out. Start again."); clear(); };
                timer.postDelayed(timeout, 600000);
                getActivity().startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(address + "/#native-login=" + encoded)));
            } catch (Exception failure) { clear(); call.reject("The system browser could not be opened."); }
        });
    }

    @PluginMethod
    public void cancelSignIn(PluginCall call) {
        getActivity().runOnUiThread(() -> {
            if (pending != null) pending.reject("Browser sign-in cancelled.");
            clear(); call.resolve();
        });
    }

    @Override
    protected void handleOnNewIntent(Intent intent) {
        Uri uri = intent.getData();
        if (uri == null || pending == null || !"freecollab".equals(uri.getScheme())
            || !"native".equals(uri.getHost()) || !"/complete".equals(uri.getPath())
            || uri.getFragment() != null || !state.equals(uri.getQueryParameter("state"))) return;
        String code = uri.getQueryParameter("code");
        if (System.currentTimeMillis() > deadline || installation() == null || code == null || !code.matches("[A-Za-z0-9_-]{43}")) {
            pending.reject("Browser sign-in is invalid or expired."); clear(); return;
        }
        JSObject result = new JSObject(); result.put("code", code); result.put("code_verifier", verifier); result.put("redirect_uri", CALLBACK);
        PluginCall completed = pending; clear(); completed.resolve(result);
        // Keep the one-use callback out of the current activity's retained intent.
        intent.setData(null);
    }

    @Override
    public Boolean shouldOverrideLoad(Uri url) {
        String address = origin(Uri.parse(getBridge().getConfig().getServerUrl()));
        if (address != null && address.equals(origin(url))) return false;
        if ("https".equals(url.getScheme()) && origin(url) != null) getActivity().runOnUiThread(() ->
            new AlertDialog.Builder(getActivity()).setTitle("Open in your browser?")
                .setMessage(origin(url)).setNegativeButton("Cancel", null)
                .setPositiveButton("Open browser", (dialog, which) -> getActivity().startActivity(new Intent(Intent.ACTION_VIEW, url))).show());
        return true;
    }

    @Override
    protected void handleOnDestroy() {
        if (pending != null) pending.reject("App closed. Start sign-in again.");
        clear();
    }
}

