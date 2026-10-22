package eu.freecollab.mobile;
import android.Manifest;
import android.net.Uri;
import com.getcapacitor.*;
import com.getcapacitor.annotation.*;
/** Every invocation stays behind the guarded modern, main-frame bridge. */
@CapacitorPlugin(name="FreeCollabCalling", permissions={@Permission(alias="microphone", strings={Manifest.permission.RECORD_AUDIO}),@Permission(alias="bluetooth", strings={Manifest.permission.BLUETOOTH_CONNECT})})
public class FreeCollabCallingPlugin extends Plugin {
    private boolean installation() {
        Uri configured=Uri.parse(getBridge().getConfig().getServerUrl()), current=Uri.parse(getBridge().getWebView().getUrl());
        return "https".equals(configured.getScheme()) && configured.getEncodedAuthority()!=null && !configured.getEncodedAuthority().contains("@") && "https".equals(current.getScheme()) && configured.getEncodedAuthority().equals(current.getEncodedAuthority());
    }
    @PluginMethod public void prepareCalling(PluginCall call) {
        if(!installation()){call.reject("The installation changed.");return;}
        if(android.os.Build.VERSION.SDK_INT<26){call.reject("Native calls need Android8 or later. Standard call alerts remain available.");return;}
        if(android.os.Build.VERSION.SDK_INT>=31 && getPermissionState("bluetooth")!=PermissionState.GRANTED){requestPermissionForAliases(new String[]{"microphone","bluetooth"},call,"microphonePermission");return;}
        if(getPermissionState("microphone")!=PermissionState.GRANTED){requestPermissionForAlias("microphone",call,"microphonePermission");return;}call.resolve();
    }
    @PermissionCallback private void microphonePermission(PluginCall call) {
        if(!installation()||getPermissionState("microphone")!=PermissionState.GRANTED){call.reject("Allow microphone access in device settings before enabling native calls.");return;}call.resolve();
    }
    @PluginMethod public void configureCalling(PluginCall call) {
        if(!installation()||getPermissionState("microphone")!=PermissionState.GRANTED){call.reject("Explicit microphone consent is required.");return;}
        getActivity().runOnUiThread(()->{try{FreeCollabCallManager.install(getContext(),getBridge().getConfig().getServerUrl(),call.getString("device_id",""),call.getString("call_secret",""));call.resolve();}catch(Exception failure){call.reject("Native calling could not be configured.");}});
    }
    @PluginMethod public void getCallState(PluginCall call) {
        if(!installation()){call.reject("The installation changed.");return;}getActivity().runOnUiThread(()->{try{call.resolve(JSObject.fromJSONObject(FreeCollabCallManager.status(getContext())));}catch(Exception failure){call.reject("Call state could not be read.");}});
    }
    @PluginMethod public void joinCall(PluginCall call) {
        if(!installation()||!Boolean.TRUE.equals(call.getBoolean("explicit_answer"))){call.reject("Explicit call consent is required.");return;}
        getActivity().runOnUiThread(()->{try{FreeCollabCallManager.join(getContext(),call.getString("room_id",""));call.resolve();}catch(Exception failure){call.reject("Native call could not be started.");}});
    }
    @PluginMethod public void answerCall(PluginCall call) {
        if(!installation()||!Boolean.TRUE.equals(call.getBoolean("explicit_answer"))){call.reject("Explicit call consent is required.");return;}
        getActivity().runOnUiThread(()->{FreeCollabCallManager.answer(getContext());call.resolve();});
    }
    @PluginMethod public void muteCall(PluginCall call) {
        if(!installation()){call.reject("The installation changed.");return;}FreeCollabCallManager.control(getContext(),Boolean.TRUE.equals(call.getBoolean("muted")),false);call.resolve();
    }
    @PluginMethod public void endCall(PluginCall call) {
        if(!installation()){call.reject("The installation changed.");return;}FreeCollabCallManager.stop(getContext());call.resolve();
    }
    @PluginMethod public void disableCalling(PluginCall call) {
        if(!installation()){call.reject("The installation changed.");return;}FreeCollabCallManager.revoke(getContext());call.resolve();
    }
}
