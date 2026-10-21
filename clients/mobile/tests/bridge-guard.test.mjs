import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const probe=spawnSync('javac',['-version'],{encoding:'utf8'});
test('application WebView executes fail-closed rejection of all-frame Java interfaces',{skip:probe.status!==0},()=>{
  const temporary=mkdtempSync(path.join(tmpdir(),'freecollab-webview-guard-'));
  const sources={
    'android/content/Context.java':'package android.content; public class Context {}',
    'android/util/AttributeSet.java':'package android.util; public interface AttributeSet {}',
    'com/getcapacitor/CapacitorWebView.java':'package com.getcapacitor; public class CapacitorWebView { public int installed=0; public CapacitorWebView(android.content.Context context,android.util.AttributeSet attributes) {} public void addJavascriptInterface(Object value,String name) { installed++; } }',
    'eu/freecollab/mobile/FreeCollabWebView.java':readFileSync(new URL('../android/app/src/main/java/eu/freecollab/mobile/FreeCollabWebView.java',import.meta.url),'utf8'),
    'eu/freecollab/mobile/GuardCheck.java':`package eu.freecollab.mobile;
      public class GuardCheck { public static void main(String[] args) {
        FreeCollabWebView guarded=new FreeCollabWebView(new android.content.Context());
        com.getcapacitor.CapacitorWebView sdkView=guarded;
        if (guarded.nativeBridgeWasRejected()) throw new AssertionError("Fresh view must have no failed listener");
        sdkView.addJavascriptInterface(new Object(),"androidBridge");
        if (!guarded.nativeBridgeWasRejected()) throw new AssertionError("Legacy fallback must be detected");
        sdkView.addJavascriptInterface(new Object(),"CapacitorCookiesAndroidInterface");
        sdkView.addJavascriptInterface(new Object(),"CapacitorHttpAndroidInterface");
        if (sdkView.installed!=0) throw new AssertionError("No all-frame Java interface may be installed");
        System.out.println("App-owned guard executed: legacy and optional interfaces rejected");
      }}
    `,
  };
  try {
    const files=[];
    for (const [relative,source] of Object.entries(sources)) { const filename=path.join(temporary,relative);mkdirSync(path.dirname(filename),{recursive:true});writeFileSync(filename,source);files.push(filename); }
    const compiled=spawnSync('javac',['--release','17','-d',temporary,...files],{encoding:'utf8'});
    assert.equal(compiled.status,0,compiled.stderr);
    const executed=spawnSync('java',['-cp',temporary,'eu.freecollab.mobile.GuardCheck'],{encoding:'utf8'});
    assert.equal(executed.status,0,executed.stderr);assert.match(executed.stdout,/interfaces rejected/);
  } finally {rmSync(temporary,{recursive:true,force:true});}
});
// The test executes the actual application subclass against inert Android type
// stubs. It does not claim an emulator, real WebView or platform compatibility.
