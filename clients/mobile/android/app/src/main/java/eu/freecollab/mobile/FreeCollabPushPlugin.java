package eu.freecollab.mobile;

import android.Manifest;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import androidx.core.app.NotificationManagerCompat;
import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;
import com.google.firebase.FirebaseApp;
import com.google.firebase.messaging.FirebaseMessaging;

/** Fixed-origin bridge. The OS permission follows the user's explicit click. */
@CapacitorPlugin(name = "FreeCollabPush", permissions = @Permission(alias = "notifications", strings = { Manifest.permission.POST_NOTIFICATIONS }))
public class FreeCollabPushPlugin extends Plugin {
    private boolean installation() {
        Uri configured = Uri.parse(getBridge().getConfig().getServerUrl());
        Uri current = Uri.parse(getBridge().getWebView().getUrl());
        return "https".equals(configured.getScheme()) && configured.getEncodedAuthority() != null
            && !configured.getEncodedAuthority().contains("@") && "https".equals(current.getScheme())
            && configured.getEncodedAuthority().equals(current.getEncodedAuthority());
    }
    static void channels(Context context) {
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationManager manager = context.getSystemService(NotificationManager.class);
            NotificationChannel activity = new NotificationChannel("freecollab_activity", "FreeCollab activity", NotificationManager.IMPORTANCE_DEFAULT);
            NotificationChannel calls = new NotificationChannel("freecollab_calls", "FreeCollab incoming calls", NotificationManager.IMPORTANCE_HIGH);
            activity.setLockscreenVisibility(android.app.Notification.VISIBILITY_PRIVATE);
            calls.setLockscreenVisibility(android.app.Notification.VISIBILITY_PRIVATE);
            manager.createNotificationChannel(activity); manager.createNotificationChannel(calls);
        }
    }
    @PluginMethod public void registerPush(PluginCall call) {
        if (!installation()) { call.reject("Native notifications require the configured installation."); return; }
        if (Build.VERSION.SDK_INT >= 33 && getPermissionState("notifications") != PermissionState.GRANTED) {
            requestPermissionForAlias("notifications", call, "notificationPermission"); return;
        }
        register(call);
    }
    @PermissionCallback private void notificationPermission(PluginCall call) {
        if (getPermissionState("notifications") != PermissionState.GRANTED) { call.reject("Notifications are disabled in device settings."); return; }
        register(call);
    }
    @PluginMethod public void refreshPush(PluginCall call) {
        if (!installation() || Build.VERSION.SDK_INT >= 33 && getPermissionState("notifications") != PermissionState.GRANTED) {
            call.reject("Notifications require explicit device consent."); return;
        }
        register(call);
    }
    private void register(PluginCall call) {
        if (!installation() || !NotificationManagerCompat.from(getContext()).areNotificationsEnabled()) {
            call.reject("Notifications are disabled or the installation changed."); return;
        }
        if (FirebaseApp.getApps(getContext()).isEmpty()) { call.reject("The operator has not configured this Android build for Firebase."); return; }
        channels(getContext());
        FirebaseMessaging.getInstance().setAutoInitEnabled(true);
        FirebaseMessaging.getInstance().getToken().addOnCompleteListener(task -> {
            if (!installation() || !task.isSuccessful() || task.getResult() == null) { call.reject("Native registration could not be completed."); return; }
            JSObject result = new JSObject(); result.put("platform", "android"); result.put("token", task.getResult()); call.resolve(result);
        });
    }
    @PluginMethod public void getPendingPush(PluginCall call) {
        if (!installation()) { call.reject("The installation changed."); return; }
        capture(getActivity().getIntent());
        JSObject result = new JSObject();
        try { result.put("items", new JSArray(getContext().getSharedPreferences("freecollab-push", Context.MODE_PRIVATE).getString("pending", "[]"))); call.resolve(result); }
        catch (Exception failure) { call.reject("Native notifications could not be read."); }
    }
    @PluginMethod public void clearPush(PluginCall call) {
        if (!installation()) { call.reject("The installation changed."); return; }
        FreeCollabPushService.clear(getContext(), call.getString("key", "")); call.resolve();
    }
    @PluginMethod public void unregisterPush(PluginCall call) {
        if (!installation()) { call.reject("The installation changed."); return; }
        FreeCollabCallManager.revoke(getContext());
        getContext().getSharedPreferences("freecollab-push", Context.MODE_PRIVATE).edit().clear().apply();
        NotificationManagerCompat.from(getContext()).cancelAll();
        if (FirebaseApp.getApps(getContext()).isEmpty()) { call.resolve(); return; }
        FirebaseMessaging.getInstance().setAutoInitEnabled(false);
        FirebaseMessaging.getInstance().deleteToken().addOnCompleteListener(task -> {
            if (task.isSuccessful()) call.resolve(); else call.reject("The device registration could not be removed.");
        });
    }
    private void capture(Intent intent) {
        if (intent == null || intent.getExtras() == null) return;
        java.util.Map<String, String> data = new java.util.HashMap<>();
        for (String key : new String[] {"type", "id", "device_id", "challenge"}) {
            String value = intent.getStringExtra(key); if (value != null) data.put(key, value);
        }
        if (FreeCollabPushService.store(getContext(), data)) {
            for (String key : data.keySet()) intent.removeExtra(key);
        }
    }
    @Override protected void handleOnNewIntent(Intent intent) { if (installation()) { capture(intent); notifyListeners("nativePush", new JSObject()); } }
    @Override protected void handleOnResume() { if (installation()) { capture(getActivity().getIntent()); notifyListeners("nativePush", new JSObject()); } }
}
