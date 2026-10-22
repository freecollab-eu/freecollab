import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync,mkdirSync,readFileSync,writeFileSync,rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
const probe=spawnSync('javac',['-version'],{encoding:'utf8'});
// Executes the actual application bridge callbacks with inert Android types.
// It does not claim Telecom, microphone, LiveKit, or physical-device delivery.
test('actual calling bridge fences origin, explicit answer and microphone setup',{skip:probe.status!==0},()=>{
 const temporary=mkdtempSync(path.join(tmpdir(),'freecollab-call-bridge-'));
 const files={
  'android/Manifest.java':'package android; public class Manifest { public static class permission {public static final String RECORD_AUDIO="mic",BLUETOOTH_CONNECT="bluetooth";} }',
  'android/os/Build.java':'package android.os; public class Build { public static class VERSION {public static int SDK_INT=36;} }',
  'android/net/Uri.java':`package android.net; public class Uri { java.net.URI u; Uri(String v){u=java.net.URI.create(v);} public static Uri parse(String v){return new Uri(v);} public String getScheme(){return u.getScheme();} public String getEncodedAuthority(){return u.getRawAuthority();} }`,
  'com/getcapacitor/PermissionState.java':'package com.getcapacitor; public enum PermissionState {GRANTED,DENIED}',
  'com/getcapacitor/PluginMethod.java':'package com.getcapacitor; public @interface PluginMethod {}',
  'com/getcapacitor/annotation/Permission.java':'package com.getcapacitor.annotation; public @interface Permission {String alias();String[] strings();}',
  'com/getcapacitor/annotation/CapacitorPlugin.java':'package com.getcapacitor.annotation; public @interface CapacitorPlugin {String name();Permission[] permissions();}',
  'com/getcapacitor/annotation/PermissionCallback.java':'package com.getcapacitor.annotation; public @interface PermissionCallback {}',
  'org/json/JSONObject.java':'package org.json; public class JSONObject {}',
  'com/getcapacitor/JSObject.java':'package com.getcapacitor; public class JSObject { public static JSObject fromJSONObject(org.json.JSONObject v)throws Exception{return new JSObject();} }',
  'com/getcapacitor/PluginCall.java':`package com.getcapacitor; public class PluginCall { public boolean resolved=false,rejected=false,answer=false;public String room="a".repeat(32); public void resolve(){resolved=true;} public void resolve(JSObject v){resolved=true;} public void reject(String reason){rejected=true;} public String getString(String key,String fallback){return key.equals("room_id")?room:key.equals("device_id")?"b".repeat(32):"c".repeat(43);} public Boolean getBoolean(String key){return answer;} }`,
  'com/getcapacitor/Plugin.java':`package com.getcapacitor; public class Plugin { public static String origin="https://collab.example.test",current=origin; public static PermissionState permission=PermissionState.DENIED;public static int prompted=0; public Bridge getBridge(){return new Bridge();} public Object getContext(){return this;} public Activity getActivity(){return new Activity();}public PermissionState getPermissionState(String name){return permission;} public void requestPermissionForAlias(String name,PluginCall call,String callback){prompted++;} public void requestPermissionForAliases(String[] names,PluginCall call,String callback){prompted++;} public static class Activity {public void runOnUiThread(Runnable action){action.run();}} public static class Bridge {public Config getConfig(){return new Config();}public View getWebView(){return new View();}}public static class Config{public String getServerUrl(){return origin;}} public static class View{public String getUrl(){return current;}} }`,
  'eu/freecollab/mobile/FreeCollabCallManager.java':`package eu.freecollab.mobile; public class FreeCollabCallManager { public static int installed=0,joined=0,answered=0,muted=0,ended=0,revoked=0; public static void install(Object c,String o,String d,String s){installed++;} public static org.json.JSONObject status(Object c){return new org.json.JSONObject();}public static void join(Object c,String id){joined++;}public static void answer(Object c){answered++;}public static void control(Object c,boolean m,boolean h){muted++;}public static void stop(Object c){ended++;}public static void revoke(Object c){revoked++;} }`,
  'eu/freecollab/mobile/FreeCollabCallIncarnation.java':readFileSync(new URL('../android/app/src/main/java/eu/freecollab/mobile/FreeCollabCallIncarnation.java',import.meta.url),'utf8'),
  'eu/freecollab/mobile/FreeCollabCallEndpoint.java':readFileSync(new URL('../android/app/src/main/java/eu/freecollab/mobile/FreeCollabCallEndpoint.java',import.meta.url),'utf8'),
  'eu/freecollab/mobile/FreeCollabCallingPlugin.java':readFileSync(new URL('../android/app/src/main/java/eu/freecollab/mobile/FreeCollabCallingPlugin.java',import.meta.url),'utf8'),
  'eu/freecollab/mobile/CallingCheck.java':`package eu.freecollab.mobile;
   import com.getcapacitor.*;
   public class CallingCheck {
    static void check(boolean value,String reason){if(!value)throw new AssertionError(reason);}
    public static void main(String[] args)throws Exception{
     check(FreeCollabCallEndpoint.url("https://collab.example.test/","offers/"+"d".repeat(32)).getPath().equals("/api/mobile/native/calling/offers/"+"d".repeat(32)),"Native requests use shipped API gateway prefix");
     for(String invalid:new String[]{"http://collab.example.test","https://user@collab.example.test","https://collab.example.test/path"}){boolean rejected=false;try{FreeCollabCallEndpoint.url(invalid,"join");}catch(Exception error){rejected=true;}check(rejected,"Untrusted API origin rejected");}
     FreeCollabCallIncarnation first=new FreeCollabCallIncarnation(),restarted=new FreeCollabCallIncarnation();
     check(first.matches(1,1,first.nonce()),"Current immutable OS action accepted");
     check(!restarted.matches(1,1,first.nonce()),"Old persistent OS action rejected after process restart even when generation repeats");
     check(!first.matches(2,1,first.nonce())&&!first.matches(1,1,null),"Previous call or missing process identity rejected");
     FreeCollabCallingPlugin plugin=new FreeCollabCallingPlugin();
     PluginCall setup=new PluginCall();plugin.prepareCalling(setup);check(Plugin.prompted==1&&!setup.resolved,"Only explicit setup asks for permission");check(FreeCollabCallManager.joined==0,"Permission setup never joins");
     PluginCall noConsent=new PluginCall();plugin.joinCall(noConsent);plugin.answerCall(noConsent);check(noConsent.rejected&&FreeCollabCallManager.joined==0,"Answer consent required");
     PluginCall denied=new PluginCall();plugin.configureCalling(denied);check(denied.rejected&&FreeCollabCallManager.installed==0,"Microphone setup permission required");
     Plugin.permission=PermissionState.GRANTED;PluginCall configured=new PluginCall();plugin.configureCalling(configured);check(configured.resolved&&FreeCollabCallManager.installed==1,"Trusted configured installation can enable");
     for(String url:new String[]{"https://evil.example.test","http://collab.example.test","https://collab.example.test.evil.test","https://user@collab.example.test"}){
      Plugin.current=url;PluginCall attempt=new PluginCall();attempt.answer=true;plugin.joinCall(attempt);plugin.answerCall(attempt);plugin.endCall(new PluginCall());plugin.disableCalling(new PluginCall());plugin.muteCall(new PluginCall());plugin.configureCalling(new PluginCall());
      check(attempt.rejected&&FreeCollabCallManager.joined==0&&FreeCollabCallManager.answered==0&&FreeCollabCallManager.ended==0&&FreeCollabCallManager.revoked==0&&FreeCollabCallManager.muted==0&&FreeCollabCallManager.installed==1,"Foreign origin cannot invoke any privileged callback");
     }
     Plugin.current=Plugin.origin;PluginCall answer=new PluginCall();answer.answer=true;plugin.joinCall(answer);check(answer.resolved&&FreeCollabCallManager.joined==1,"Explicit answer from installation handed off once");
     android.os.Build.VERSION.SDK_INT=24;PluginCall old=new PluginCall();plugin.prepareCalling(old);check(old.rejected&&Plugin.prompted==1,"Older Android keeps safe alert fallback");
     System.out.println("Actual native calling callbacks executed: origin/answer/permission fences passed");
    }
   }`,
 };
 try{
  const sources=[];for(const [relative,source]of Object.entries(files)){const target=path.join(temporary,relative);mkdirSync(path.dirname(target),{recursive:true});writeFileSync(target,source);sources.push(target);}
  const compile=spawnSync('javac',['--release','17','-d',temporary,...sources],{encoding:'utf8'});assert.equal(compile.status,0,compile.stderr);
  const execute=spawnSync('java',['-cp',temporary,'eu.freecollab.mobile.CallingCheck'],{encoding:'utf8'});assert.equal(execute.status,0,execute.stderr);assert.match(execute.stdout,/fences passed/);
 }finally{rmSync(temporary,{recursive:true,force:true});}
});
