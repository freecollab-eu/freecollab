package eu.freecollab.mobile

import com.getcapacitor.CapConfig
import android.Manifest
import android.app.*
import android.content.*
import android.content.pm.PackageManager
import android.net.Uri
import android.os.*
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.telecom.*
import android.util.Base64
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import androidx.core.app.Person
import io.livekit.android.AudioOptions
import io.livekit.android.LiveKitOverrides
import io.livekit.android.events.RoomEvent
import io.livekit.android.events.collect
import io.livekit.android.util.LoggingLevel
import livekit.org.webrtc.audio.JavaAudioDeviceModule
import io.livekit.android.LiveKit
import io.livekit.android.ConnectOptions
import io.livekit.android.room.Room
import kotlinx.coroutines.*
import org.json.JSONObject
import java.net.HttpURLConnection
import java.security.KeyStore
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** No account bearer or Matrix key enters this component. OS Answer is consent;
 * ringing never joins, subscribes to media, or activates the microphone. */
object FreeCollabCallManager {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var room: Room? = null
    private var audioModule: JavaAudioDeviceModule? = null
    private var mediaEvents: Job? = null
    private var controlGeneration = 0
    private var connection: Connection? = null
    private var pending: String? = null
    private var sessionId: String? = null
    private var muted = false
    private var requestedMuted = false
    private var systemMuted = false
    private var held = false
    private var generation = 0
    private val incarnation = FreeCollabCallIncarnation()
    private val processNonce = incarnation.nonce()
    private var heartbeat: Job? = null
    private var ringingMonitor: Job? = null
    private var watchdog: Job? = null
    private var lastAuthorized = 0L
    private var phase = "idle"
    private const val NOTICE = 7631
    private fun prefs(c: Context) = c.getSharedPreferences("freecollab-calling", Context.MODE_PRIVATE)
    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore"); store.load(null)
        (store.getKey("freecollab-call-capability", null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(KeyGenParameterSpec.Builder("freecollab-call-capability", KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
        return generator.generateKey()
    }
    private fun credentials(c: Context): JSONObject? { return try {
        val encoded = prefs(c).getString("capability", null) ?: return null
        val bytes = Base64.decode(encoded, Base64.NO_WRAP)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, bytes.copyOfRange(0, 12)))
        val saved = JSONObject(String(cipher.doFinal(bytes.copyOfRange(12, bytes.size)), Charsets.UTF_8))
        // A rebuilt app for another installation must never restore the former
        // installation's background capability before its WebView starts.
        val configured = Uri.parse(CapConfig.loadDefault(c).serverUrl)
        val selected = Uri.parse(saved.getString("origin"))
        if (configured.scheme != "https" || configured.encodedAuthority == null || configured.userInfo != null
            || selected.scheme != "https" || selected.encodedAuthority != configured.encodedAuthority || selected.userInfo != null
            || !selected.path.isNullOrEmpty() || selected.query != null || selected.fragment != null
            || !saved.optString("device_id").matches(Regex("[a-f0-9]{32}"))
            || !saved.optString("secret").matches(Regex("[A-Za-z0-9_-]{43}"))) return null
        saved
    } catch (_: Exception) { null } }
    @JvmStatic fun install(c: Context, origin: String, device: String, secret: String) {
        require(Build.VERSION.SDK_INT >= 26)
        val uri = Uri.parse(origin)
        require(uri.scheme == "https" && uri.host != null && uri.userInfo == null && uri.query == null && uri.fragment == null && (uri.path.isNullOrEmpty() || uri.path == "/"))
        require(device.matches(Regex("[a-f0-9]{32}")) && secret.matches(Regex("[A-Za-z0-9_-]{43}")))
        stop(c); val cipher = Cipher.getInstance("AES/GCM/NoPadding"); cipher.init(Cipher.ENCRYPT_MODE, key())
        val value = JSONObject().put("origin", origin.trimEnd('/')).put("device_id", device).put("secret", secret).toString()
        val encrypted = cipher.iv + cipher.doFinal(value.toByteArray(Charsets.UTF_8))
        prefs(c).edit().putString("capability", Base64.encodeToString(encrypted, Base64.NO_WRAP)).commit()
        val account = PhoneAccount.builder(handle(c), "FreeCollab").setCapabilities(PhoneAccount.CAPABILITY_SELF_MANAGED).build()
        c.getSystemService(TelecomManager::class.java).registerPhoneAccount(account)
    }
    @JvmStatic fun configured(c: Context) = Build.VERSION.SDK_INT >= 26 && credentials(c) != null
    private fun handle(c: Context) = PhoneAccountHandle(ComponentName(c, FreeCollabConnectionService::class.java), "freecollab-native-audio")
    private suspend fun request(c: Context, path: String, body: JSONObject? = null, method: String = "POST", captured: JSONObject? = null): JSONObject = withContext(Dispatchers.IO) {
        val credential = captured ?: credentials(c) ?: error("Calling is not enabled")
        val destination = FreeCollabCallEndpoint.url(credential.getString("origin"), path)
        val http = destination.openConnection() as HttpURLConnection
        try {
            http.instanceFollowRedirects = false; http.connectTimeout = 5000; http.readTimeout = 5000; http.requestMethod = method
            http.setRequestProperty("Authorization", "Bearer " + credential.getString("secret"))
            http.setRequestProperty("X-FreeCollab-Call-Device", credential.getString("device_id"))
            http.setRequestProperty("Content-Type", "application/json")
            if (body != null) { http.doOutput = true; http.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) } }
            if (http.responseCode !in 200..299) error("Calling authorization unavailable")
            val data = http.inputStream.use { stream ->
                val output = java.io.ByteArrayOutputStream(); val buffer = ByteArray(4096)
                while (true) { val count = stream.read(buffer); if (count < 0) break; output.write(buffer, 0, count); if (output.size() > 65536) error("Calling response too large") }; output.toByteArray()
            }
            if (data.size > 65536) error("Calling response too large")
            if (data.isEmpty()) JSONObject() else JSONObject(String(data, Charsets.UTF_8))
        } finally { http.disconnect() }
    }
    @JvmStatic fun incoming(c: Context, id: String, device: String): Boolean {
        if (Build.VERSION.SDK_INT < 26) return false
        val credential = credentials(c) ?: return false
        if (!id.matches(Regex("[a-f0-9]{32}")) || credential.optString("device_id") != device) return false
        scope.launch {
            if (phase != "idle" || credentials(c)?.optString("device_id") != device) return@launch
            val expected = generation
            try {
                request(c, "offers/$id", method = "GET", captured = credential)
                if (expected != generation || phase != "idle" || credentials(c)?.optString("device_id") != device) return@launch
                pending = id; phase = "ringing"; generation++
                val extras = Bundle(); extras.putString("freecollab_call_id", id); extras.putInt("freecollab_generation", generation); extras.putString("freecollab_process", processNonce)
                c.getSystemService(TelecomManager::class.java).addNewIncomingCall(handle(c), extras)
                val ringGeneration = generation
                ringingMonitor = scope.launch {
                    while (generation == ringGeneration && phase == "ringing") {
                        delay(5000)
                        if (generation != ringGeneration || phase != "ringing") return@launch
                        try { request(c, "offers/$id", method = "GET", captured = credential) }
                        catch (_: Exception) { if (generation == ringGeneration && phase == "ringing") stop(c); break }
                    }
                }
                // Bound ringing even if the OS never creates a connection.
                scope.launch { delay(120000); if (generation == ringGeneration && phase == "ringing") stop(c) }
            } catch (_: Exception) { if (generation == expected || generation == expected + 1 && pending == id && phase == "ringing") stop(c) }
        }
        return true
    }
    @JvmStatic fun attach(c: Context, call: Connection, incoming: Boolean, expected: Int, nonce: String?): Boolean {
        if (!current(expected, nonce)) return false
        if (incoming && (pending == null || phase !in setOf("ringing", "connecting", "active")) || !incoming && phase !in setOf("connecting", "active")) return false
        connection = call
        call.connectionProperties = Connection.PROPERTY_SELF_MANAGED
        call.connectionCapabilities = Connection.CAPABILITY_SUPPORT_HOLD or Connection.CAPABILITY_HOLD or Connection.CAPABILITY_MUTE
        call.setAddress(Uri.parse("freecollab:call"), TelecomManager.PRESENTATION_RESTRICTED)
        call.setCallerDisplayName("FreeCollab call", TelecomManager.PRESENTATION_ALLOWED)
        call.setAudioModeIsVoip(true)
        if (incoming && phase == "ringing") { call.setRinging(); show(c, true) } else if (incoming || phase == "active") call.setActive() else call.setDialing()
        return true
    }
    private fun action(c: Context, value: String) = PendingIntent.getBroadcast(c, value.hashCode(), Intent(c, FreeCollabCallAction::class.java).setAction(value).setData(Uri.parse("freecollab-call-action://current/$processNonce/$generation/$value")).putExtra("freecollab_generation", generation).putExtra("freecollab_process", processNonce), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    @JvmStatic fun notification(c: Context, ringing: Boolean): Notification {
        FreeCollabPushPlugin.channels(c)
        val open = PendingIntent.getActivity(c, NOTICE, Intent(c, MainActivity::class.java), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val person = Person.Builder().setName("FreeCollab call").setImportant(true).build()
        val builder = NotificationCompat.Builder(c, "freecollab_calls").setSmallIcon(R.mipmap.ic_launcher).setContentTitle("FreeCollab call")
            .setContentText(if (ringing) "Incoming audio call" else if (held) "Call on hold" else if (muted) "Microphone muted" else "Audio call in progress")
            .setContentIntent(open).setVisibility(NotificationCompat.VISIBILITY_PRIVATE).setCategory(NotificationCompat.CATEGORY_CALL).setOngoing(!ringing)
        if (ringing) builder.setStyle(NotificationCompat.CallStyle.forIncomingCall(person, action(c, "decline"), action(c, "answer"))).setTimeoutAfter(120000)
        else builder.setStyle(NotificationCompat.CallStyle.forOngoingCall(person, action(c, "end"))).addAction(0, if (muted) "Unmute" else "Mute", action(c, if (muted) "unmute" else "mute"))
        return builder.build()
    }
    private fun show(c: Context, ringing: Boolean) { try { c.getSystemService(NotificationManager::class.java).notify(NOTICE, notification(c, ringing)) } catch (_: SecurityException) {} }
    @JvmStatic fun answer(c: Context) { val id = pending ?: return; begin(c, "offers/$id/answer", id) }
    @JvmStatic fun join(c: Context, roomId: String) { require(roomId.matches(Regex("[a-f0-9]{32}"))); begin(c, "join", UUID.randomUUID().toString().replace("-", ""), roomId) }
    private fun begin(c: Context, path: String, callId: String, roomId: String? = null) {
        if (phase == "connecting" || phase == "active" || roomId != null && phase != "idle") return
        if (ContextCompat.checkSelfPermission(c, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) { stop(c); return }
        val captured = credentials(c) ?: return
        ringingMonitor?.cancel(); ringingMonitor = null
        val expected = if (phase == "ringing") generation else ++generation
        phase = "connecting"
        if (roomId != null) {
            try { val extras = Bundle(); extras.putParcelable(TelecomManager.EXTRA_PHONE_ACCOUNT_HANDLE, handle(c)); val outgoing = Bundle(); outgoing.putInt("freecollab_generation", expected); outgoing.putString("freecollab_process", processNonce); extras.putBundle(TelecomManager.EXTRA_OUTGOING_CALL_EXTRAS, outgoing); c.getSystemService(TelecomManager::class.java).placeCall(Uri.parse("freecollab:call"), extras) }
            catch (_: Exception) { stop(c); return }
        }
        try { ContextCompat.startForegroundService(c, Intent(c, FreeCollabCallService::class.java).putExtra("freecollab_generation", expected).putExtra("freecollab_process", processNonce)) } catch (_: Exception) { stop(c); return }
        scope.launch {
            try {
                val body = JSONObject().put("explicit_answer", true).put("client_call_id", callId)
                if (roomId != null) body.put("room_id", roomId)
                val access = request(c, path, body, captured = captured)
                if (expected != generation) { request(c,"sessions/${access.getString("session_id")}/end",JSONObject(),captured=captured); return@launch }
                sessionId = access.getString("session_id"); lastAuthorized = SystemClock.elapsedRealtime()
                watchdog = scope.launch { while (expected == generation) { delay(1000); if (expected == generation && SystemClock.elapsedRealtime() - lastAuthorized > 15000) { stop(c); break } } }
                val media = Uri.parse(access.getString("url")); require(media.scheme == "wss" && media.host != null && media.userInfo == null && media.query == null && media.fragment == null && media.path.isNullOrEmpty())
                LiveKit.loggingLevel = LoggingLevel.OFF; LiveKit.enableWebRTCLogging = false
                LiveKit.init(c.applicationContext)
                val audio = JavaAudioDeviceModule.builder(c.applicationContext).setUseHardwareAcousticEchoCanceler(true).setUseHardwareNoiseSuppressor(true).createAudioDeviceModule()
                audio.setMicrophoneMute(true); audio.setSpeakerMute(true); audioModule = audio
                val next = LiveKit.create(c.applicationContext, overrides = LiveKitOverrides(audioOptions = AudioOptions(audioDeviceModule = audio, disableAudioPrewarming = true))); room = next
                mediaEvents = scope.launch {
                    next.events.collect { event ->
                        if (expected != generation) return@collect
                        when (event) {
                            is RoomEvent.Disconnected -> stop(c)
                            is RoomEvent.Reconnecting -> { audio.setMicrophoneMute(true); audio.setSpeakerMute(true); phase = "connecting" }
                            is RoomEvent.Reconnected -> { control(c, requestedMuted, held); phase = "active" }
                            else -> {}
                        }
                    }
                }
                next.connect(access.getString("url"), access.getString("token"), ConnectOptions(audio = false, video = false))
                if (expected != generation) { next.disconnect(); next.release(); return@launch }
                // Reauthorize after transport connection, before enabling capture.
                request(c, "sessions/${sessionId}/heartbeat", JSONObject().put("muted", requestedMuted || systemMuted).put("held", held), captured = captured)
                if (expected != generation) return@launch
                lastAuthorized = SystemClock.elapsedRealtime()
                muted = requestedMuted || systemMuted
                val initialControl = controlGeneration
                next.localParticipant.setMicrophoneEnabled(!muted && !held)
                if (expected != generation) return@launch
                if (initialControl == controlGeneration) { audio.setMicrophoneMute(muted || held); audio.setSpeakerMute(held) }
                phase = "active"; if (held) connection?.setOnHold() else connection?.setActive(); show(c, false)
                heartbeat = scope.launch {
                    while (expected == generation) {
                        delay(5000)
                        if (expected != generation) return@launch
                        try { request(c, "sessions/${sessionId}/heartbeat", JSONObject().put("muted", muted).put("held", held), captured = captured); if (expected == generation) lastAuthorized = SystemClock.elapsedRealtime() }
                        catch (_: Exception) { if (expected == generation) stop(c); break }
                    }
                }
            } catch (_: Exception) { if (expected == generation) stop(c) }
        }
    }
    @JvmStatic fun control(c: Context, mute: Boolean, hold: Boolean = held) {
        if (Looper.myLooper() != Looper.getMainLooper()) { Handler(Looper.getMainLooper()).post { control(c, mute, hold) }; return }
        val id = sessionId ?: return; val captured = credentials(c) ?: return; val expected = generation; val control = ++controlGeneration
        requestedMuted = mute; val effectiveMute = mute || systemMuted
        muted = effectiveMute; held = hold
        // The audio-device gate is independent of asynchronous SDK publish
        // operations. An old unmute completion cannot reverse a newer mute.
        if (effectiveMute || hold) audioModule?.setMicrophoneMute(true)
        if (hold) audioModule?.setSpeakerMute(true)
        // Disabling capture is immediate; enabling always needs fresh permission.
        if (effectiveMute || hold) scope.launch { room?.localParticipant?.setMicrophoneEnabled(false) }
        scope.launch {
            try { request(c, "sessions/$id/heartbeat", JSONObject().put("muted", effectiveMute).put("held", hold), captured = captured)
                if (expected != generation || control != controlGeneration) return@launch
                lastAuthorized = SystemClock.elapsedRealtime()
                room?.localParticipant?.setMicrophoneEnabled(!effectiveMute && !hold)
                if (expected != generation || control != controlGeneration) return@launch
                audioModule?.setMicrophoneMute(effectiveMute || hold); audioModule?.setSpeakerMute(hold)
                if (hold) connection?.setOnHold() else connection?.setActive()
                show(c, false)
            } catch (_: Exception) { if (expected == generation && control == controlGeneration) stop(c) }
        }
    }
    @JvmStatic fun owns(call: Connection) = connection === call
    @JvmStatic fun current(expected: Int, nonce: String?) = incarnation.matches(generation, expected, nonce)
    @JvmStatic fun dispatchAction(c: Context, value: String?, expected: Int, nonce: String?) { if (!current(expected, nonce)) return; when (value) { "answer" -> answer(c); "mute" -> control(c, true); "unmute" -> control(c, false); "end", "decline" -> stop(c) } }
    @JvmStatic fun toggleMute(c: Context) = control(c, !requestedMuted)
    @JvmStatic fun hold(c: Context, value: Boolean) = control(c, requestedMuted, value)
    @JvmStatic fun systemMute(c: Context, value: Boolean) { if (systemMuted == value) return; systemMuted = value; control(c, requestedMuted, held) }
    @JvmStatic fun stop(c: Context) {
        if (Looper.myLooper() != Looper.getMainLooper()) { Handler(Looper.getMainLooper()).post { stop(c) }; return }
        val id = sessionId; val credential = credentials(c); generation++; controlGeneration++; ringingMonitor?.cancel(); ringingMonitor = null; audioModule?.setMicrophoneMute(true); audioModule?.setSpeakerMute(true); mediaEvents?.cancel(); mediaEvents = null; heartbeat?.cancel(); watchdog?.cancel(); heartbeat = null; watchdog = null
        room?.disconnect(); room?.release(); room = null
        audioModule?.release(); audioModule = null
        connection?.setDisconnected(DisconnectCause(DisconnectCause.LOCAL)); connection?.destroy(); connection = null
        pending = null; sessionId = null; phase = "idle"; muted = false; requestedMuted = false; systemMuted = false; held = false
        c.getSystemService(NotificationManager::class.java).cancel(NOTICE); c.stopService(Intent(c, FreeCollabCallService::class.java))
        if (id != null && credential != null) scope.launch { try { request(c,"sessions/$id/end",JSONObject(),captured=credential) } catch (_: Exception) {} }
    }
    @JvmStatic fun revoke(c: Context) {
        if (Looper.myLooper() != Looper.getMainLooper()) { Handler(Looper.getMainLooper()).post { revoke(c) }; return }
        val credential = credentials(c); stop(c)
        // Capability is removed before any network wait. A delayed logout must
        // never erase a subsequently configured replacement credential.
        prefs(c).edit().clear().commit()
        try { val store = KeyStore.getInstance("AndroidKeyStore"); store.load(null); store.deleteEntry("freecollab-call-capability") } catch (_: Exception) {}
        if (credential != null) scope.launch { try { request(c,"device",method="DELETE",captured=credential) } catch (_: Exception) {} }
    }
    @JvmStatic fun status(c: Context): JSONObject = JSONObject().put("configured", configured(c)).put("device_id", credentials(c)?.optString("device_id")).put("phase", phase).put("muted", muted).put("held", held).put("audio_e2ee", false)
}

class FreeCollabConnectionService : ConnectionService() {
    override fun onCreateIncomingConnection(handle: PhoneAccountHandle?, request: ConnectionRequest?): Connection {
        val call = object : Connection() {
            override fun onAnswer() { if (FreeCollabCallManager.owns(this)) FreeCollabCallManager.answer(this@FreeCollabConnectionService) }
            override fun onAnswer(videoState: Int) { onAnswer() }
            override fun onReject() { if (FreeCollabCallManager.owns(this)) FreeCollabCallManager.stop(this@FreeCollabConnectionService) }
            override fun onDisconnect() { if (FreeCollabCallManager.owns(this)) FreeCollabCallManager.stop(this@FreeCollabConnectionService) }
            override fun onHold() { if (FreeCollabCallManager.owns(this)) FreeCollabCallManager.hold(this@FreeCollabConnectionService, true) }
            override fun onUnhold() { if (FreeCollabCallManager.owns(this)) FreeCollabCallManager.hold(this@FreeCollabConnectionService, false) }
            override fun onCallAudioStateChanged(state: CallAudioState?) { if (state != null && FreeCollabCallManager.owns(this)) FreeCollabCallManager.systemMute(this@FreeCollabConnectionService, state.isMuted) }
            override fun onShowIncomingCallUi() { /* App-owned private CallStyle notification already shown. */ }
        }
        return if (FreeCollabCallManager.attach(this, call, true, request?.extras?.getInt("freecollab_generation", -1) ?: -1, request?.extras?.getString("freecollab_process"))) call else Connection.createFailedConnection(DisconnectCause(DisconnectCause.ERROR))
    }
    override fun onCreateOutgoingConnection(handle: PhoneAccountHandle?, request: ConnectionRequest?): Connection {
        val call = object : Connection() {
            override fun onDisconnect() { if (FreeCollabCallManager.owns(this)) FreeCollabCallManager.stop(this@FreeCollabConnectionService) }
            override fun onAbort() { if (FreeCollabCallManager.owns(this)) FreeCollabCallManager.stop(this@FreeCollabConnectionService) }
            override fun onCallAudioStateChanged(state: CallAudioState?) { if (state != null && FreeCollabCallManager.owns(this)) FreeCollabCallManager.systemMute(this@FreeCollabConnectionService, state.isMuted) }
            override fun onHold() { if (FreeCollabCallManager.owns(this)) FreeCollabCallManager.hold(this@FreeCollabConnectionService, true) }
            override fun onUnhold() { if (FreeCollabCallManager.owns(this)) FreeCollabCallManager.hold(this@FreeCollabConnectionService, false) }
        }
        return if (FreeCollabCallManager.attach(this, call, false, request?.extras?.getInt("freecollab_generation", -1) ?: -1, request?.extras?.getString("freecollab_process"))) call else Connection.createFailedConnection(DisconnectCause(DisconnectCause.ERROR))
    }
    override fun onCreateOutgoingConnectionFailed(handle: PhoneAccountHandle?, request: ConnectionRequest?) { if (FreeCollabCallManager.current(request?.extras?.getInt("freecollab_generation", -1) ?: -1, request?.extras?.getString("freecollab_process"))) FreeCollabCallManager.stop(this) }
    override fun onCreateIncomingConnectionFailed(handle: PhoneAccountHandle?, request: ConnectionRequest?) { if (FreeCollabCallManager.current(request?.extras?.getInt("freecollab_generation", -1) ?: -1, request?.extras?.getString("freecollab_process"))) FreeCollabCallManager.stop(this) }
}

class FreeCollabCallService : Service() {
    private var callGeneration = -1
    private var callProcess: String? = null
    override fun onBind(intent: Intent?) = null
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        callGeneration = intent?.getIntExtra("freecollab_generation", -1) ?: -1
        callProcess = intent?.getStringExtra("freecollab_process")
        if (!FreeCollabCallManager.current(callGeneration, callProcess)) { stopSelf(startId); return START_NOT_STICKY }
        try {
            val types = if (Build.VERSION.SDK_INT >= 30) android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_PHONE_CALL or android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE else 0
            if (Build.VERSION.SDK_INT >= 29) startForeground(7631, FreeCollabCallManager.notification(this, false), types) else startForeground(7631, FreeCollabCallManager.notification(this, false))
        } catch (_: Exception) { if (FreeCollabCallManager.current(callGeneration, callProcess)) FreeCollabCallManager.stop(this) }
        return START_NOT_STICKY
    }
    override fun onDestroy() { if (FreeCollabCallManager.current(callGeneration, callProcess)) FreeCollabCallManager.stop(this); super.onDestroy() }
}
class FreeCollabCallAction : BroadcastReceiver() {
    override fun onReceive(c: Context, intent: Intent) { FreeCollabCallManager.dispatchAction(c, intent.action, intent.getIntExtra("freecollab_generation", -1), intent.getStringExtra("freecollab_process")) }
}
