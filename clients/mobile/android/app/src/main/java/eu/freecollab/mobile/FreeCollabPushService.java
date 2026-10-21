package eu.freecollab.mobile;

import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;
import com.google.firebase.messaging.FirebaseMessagingService;
import com.google.firebase.messaging.RemoteMessage;
import java.util.Map;
import org.json.JSONArray;
import org.json.JSONObject;

/** Foreground delivery + generic OS alerts; no full-screen intent or media start. */
public class FreeCollabPushService extends FirebaseMessagingService {
    static boolean store(Context context, Map<String, String> data) {
        String type = data.get("type"), key;
        if ("subscription_confirmation".equals(type)) {
            if (!valid(data.get("device_id"), 32) || !valid(data.get("challenge"), 43)) return false;
            key = data.get("device_id");
        } else if ("activity".equals(type) || "incoming_call".equals(type)) {
            if (!valid(data.get("id"), 32)) return false;
            key = data.get("id");
        } else return false;
        try {
            JSONObject item = new JSONObject(); item.put("key", key); item.put("type", type);
            for (String name : new String[] {"id", "device_id", "challenge"}) if (data.containsKey(name)) item.put(name, data.get(name));
            synchronized (FreeCollabPushService.class) {
                android.content.SharedPreferences preferences = context.getSharedPreferences("freecollab-push", Context.MODE_PRIVATE);
                JSONArray prior = new JSONArray(preferences.getString("pending", "[]")), next = new JSONArray();
                for (int i = Math.max(0, prior.length() - 9); i < prior.length(); i++) if (!key.equals(prior.getJSONObject(i).optString("key"))) next.put(prior.getJSONObject(i));
                next.put(item); preferences.edit().putString("pending", next.toString()).apply();
            }
            return true;
        } catch (Exception failure) { return false; }
    }
    static void clear(Context context, String key) {
        synchronized (FreeCollabPushService.class) {
            try {
                android.content.SharedPreferences preferences = context.getSharedPreferences("freecollab-push", Context.MODE_PRIVATE);
                JSONArray prior = new JSONArray(preferences.getString("pending", "[]")), next = new JSONArray();
                for (int i = 0; i < prior.length(); i++) if (!key.equals(prior.getJSONObject(i).optString("key"))) next.put(prior.getJSONObject(i));
                preferences.edit().putString("pending", next.toString()).apply();
            } catch (Exception ignored) { }
        }
    }
    private static boolean valid(String value, int length) { return value != null && value.matches("[A-Za-z0-9_-]{" + length + "}"); }
    @Override public void onMessageReceived(RemoteMessage message) {
        Map<String, String> data = message.getData();
        if (!store(this, data)) return;
        FreeCollabPushPlugin.channels(this);
        String key = data.containsKey("id") ? data.get("id") : data.get("device_id");
        Intent intent = new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        for (Map.Entry<String, String> entry : data.entrySet()) if (entry.getKey().matches("type|id|device_id|challenge")) intent.putExtra(entry.getKey(), entry.getValue());
        PendingIntent open = PendingIntent.getActivity(this, key.hashCode(), intent, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        boolean call = "incoming_call".equals(data.get("type"));
        NotificationCompat.Builder notice = new NotificationCompat.Builder(this, call ? "freecollab_calls" : "freecollab_activity")
            .setSmallIcon(eu.freecollab.mobile.R.mipmap.ic_launcher).setContentTitle("FreeCollab")
            .setContentText(call ? "Incoming FreeCollab call" : "New FreeCollab activity").setContentIntent(open)
            .setAutoCancel(true).setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setCategory(call ? NotificationCompat.CATEGORY_CALL : NotificationCompat.CATEGORY_MESSAGE)
            .setPriority(call ? NotificationCompat.PRIORITY_HIGH : NotificationCompat.PRIORITY_DEFAULT).setTimeoutAfter(call ? 120000 : 300000);
        try { NotificationManagerCompat.from(this).notify(key.hashCode(), notice.build()); }
        catch (SecurityException ignored) { /* Explicitly denied OS permission remains denied. */ }
    }
}
