import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const probe = spawnSync('swiftc', ['--version'], { encoding: 'utf8' });
// Runs the actual Foundation-only app policy on Swift-equipped builders. This
// is neither an iPhone test nor a replacement for compiling the actual SDKs.
test('iOS native callback policy requires consent, current generation, OS audio activation and fresh authority', { skip: probe.status !== 0 }, () => {
  const temporary = mkdtempSync(path.join(tmpdir(), 'freecollab-ios-policy-'));
  try {
    const main = path.join(temporary, 'main.swift');
    writeFileSync(main, `import Foundation
func check(_ value: Bool, _ reason: String) { if !value { fatalError(reason) } }
check(FreeCollabCallingPolicy.origin("https://COLLAB.example.test:443/") == "https://collab.example.test", "Canonical origin")
check(FreeCollabCallingPolicy.origin("https://collab.example.test:8443") == "https://collab.example.test:8443", "Explicit customer port")
for address in ["http://collab.example.test", "https://user:secret@collab.example.test", "https://collab.example.test/path", "https://collab.example.test/?token=x", "https://collab.example.test/#token=x", " https://collab.example.test"] {
 check(FreeCollabCallingPolicy.origin(address) == nil, "Reject untrusted origin form")
}
check(FreeCollabCallingPolicy.apiURL(origin:"https://collab.example.test",path:"offers/" + String(repeating:"a",count:32))?.absoluteString == "https://collab.example.test/api/mobile/native/calling/offers/" + String(repeating:"a",count:32), "Deployed same-origin API prefix")
for path in ["https://evil.example.test", "/device", "../device", "device?token=x", ""] { check(FreeCollabCallingPolicy.apiURL(origin:"https://collab.example.test",path:path) == nil, "Fixed API path only") }
check(FreeCollabCallingPolicy.identifier(String(repeating:"a",count:32)) != nil, "Opaque call identifier")
for value in [String(repeating:"A",count:32), String(repeating:"a",count:31), "../../private"] { check(FreeCollabCallingPolicy.identifier(value) == nil, "Reject invalid identifier") }
check(FreeCollabCallingPolicy.mediaAllowed(generation:1,expected:1,explicitAnswer:true,audioActivated:true,mediaReady:true), "Explicit current authorized audio")
check(!FreeCollabCallingPolicy.mediaAllowed(generation:1,expected:1,explicitAnswer:false,audioActivated:true,mediaReady:true), "Incoming push is not consent")
check(!FreeCollabCallingPolicy.mediaAllowed(generation:2,expected:1,explicitAnswer:true,audioActivated:true,mediaReady:true), "Stale completion cannot enable media")
check(!FreeCollabCallingPolicy.mediaAllowed(generation:1,expected:1,explicitAnswer:true,audioActivated:false,mediaReady:true), "CallKit exclusively activates audio")
check(!FreeCollabCallingPolicy.mediaAllowed(generation:1,expected:1,explicitAnswer:true,audioActivated:true,mediaReady:false), "Signalling alone does not start media")
check(!FreeCollabCallingPolicy.authorizationExpired(lastSuccessful:10,now:24.99), "Bounded valid lease")
check(FreeCollabCallingPolicy.authorizationExpired(lastSuccessful:10,now:25), "Fail closed at15seconds")
check(FreeCollabCallingPolicy.authorizationExpired(lastSuccessful:10,now:9), "Clock anomaly fails closed")
check(FreeCollabCallingPolicy.authorizationExpired(lastSuccessful:Double.nan,now:10), "Invalid clock fails closed")
let old = UUID(), current = UUID()
check(!FreeCollabCallingPolicy.actionOwnsCall(action:old,current:current), "Delayed OS end cannot affect replacement call")
check(!FreeCollabCallingPolicy.actionOwnsCall(action:old,current:nil), "No call means no old action authority")
check(FreeCollabCallingPolicy.actionOwnsCall(action:current,current:current), "Current OS action ownership")
print("Actual iOS calling policy executed: origin, consent, OS audio, generation and watchdog fences passed")
`);
    const executable = path.join(temporary, 'policy');
    const policy = fileURLToPath(new URL('../ios/App/App/FreeCollabCallingPolicy.swift', import.meta.url));
    const compiled = spawnSync('swiftc', [policy, main, '-o', executable], { encoding: 'utf8' });
    assert.equal(compiled.status, 0, compiled.stderr);
    const executed = spawnSync(executable, [], { encoding: 'utf8' });
    assert.equal(executed.status, 0, executed.stderr);
    assert.match(executed.stdout, /watchdog fences passed/);
  } finally { rmSync(temporary, { recursive: true, force: true }); }
});
