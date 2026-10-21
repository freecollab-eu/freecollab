package eu.freecollab.mobile;

import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import androidx.webkit.WebViewFeature;
import com.getcapacitor.BridgeActivity;
import com.getcapacitor.CapConfig;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle state) {
        registerPlugin(FreeCollabNativePlugin.class);
        registerPlugin(FreeCollabPushPlugin.class);
        super.onCreate(state);
    }
    @Override
    protected void load() {
        CapConfig selected = config != null ? config : CapConfig.loadDefault(this);
        if (!(findViewById(eu.freecollab.mobile.R.id.webview) instanceof FreeCollabWebView)
                || !WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER) || selected.isUsingLegacyBridge()) {
            unsupported(selected.getServerUrl()); return;
        }
        super.load();
        if (!(bridge.getWebView() instanceof FreeCollabWebView)
                || ((FreeCollabWebView) bridge.getWebView()).nativeBridgeWasRejected()) {
            // The app-priority layout must select the guarded view before Bridge
            // construction. A failed modern listener never installs a fallback.
            bridge.getWebView().stopLoading(); bridge.getWebView().loadUrl("about:blank");
            unsupported(selected.getServerUrl());
        }
    }
    private void unsupported(String address) {
        LinearLayout layout = new LinearLayout(this); layout.setOrientation(LinearLayout.VERTICAL);
        int padding = (int) (24 * getResources().getDisplayMetrics().density); layout.setPadding(padding,padding,padding,padding);
        TextView title = new TextView(this); title.setText("Update Android System WebView"); title.setTextSize(24);
        TextView detail = new TextView(this); detail.setText("FreeCollab needs a current WebView with a protected native bridge. Update Android System WebView or use your system browser.");
        layout.addView(title); layout.addView(detail);
        if (address != null) {
            Uri destination = Uri.parse(address);
            if ("https".equals(destination.getScheme()) && destination.getEncodedAuthority() != null && !destination.getEncodedAuthority().contains("@")) {
                Button open = new Button(this); open.setText("Open FreeCollab in browser");
                open.setOnClickListener(view -> startActivity(new Intent(Intent.ACTION_VIEW,destination))); layout.addView(open);
            }
        }
        setContentView(layout);
    }
}
