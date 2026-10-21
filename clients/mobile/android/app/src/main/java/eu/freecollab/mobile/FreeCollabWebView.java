package eu.freecollab.mobile;

import android.content.Context;
import android.util.AttributeSet;
import com.getcapacitor.CapacitorWebView;

/** Never expose an all-frame Java interface, even if the modern listener fails. */
public final class FreeCollabWebView extends CapacitorWebView {
    private boolean rejectedNativeBridge;
    public FreeCollabWebView(Context context) { super(context, null); }
    public FreeCollabWebView(Context context, AttributeSet attributes) { super(context, attributes); }
    @Override
    public void addJavascriptInterface(Object object, String name) {
        // WebViewCompat.addWebMessageListener is a separate, origin/main-frame
        // aware API. Capacitor's fallback and optional direct Java interfaces
        // have no caller-frame proof, so this application never installs them.
        if ("androidBridge".equals(name)) rejectedNativeBridge = true;
    }
    public boolean nativeBridgeWasRejected() { return rejectedNativeBridge; }
}
