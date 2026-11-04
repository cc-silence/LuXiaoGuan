# IPsec review 2: three code rounds plus three additional documentation rounds

Checked on 2026-11-04, after the previous 117-test review. User then requested an
additional three documentation rounds while work was active. No live network
policy was created, removed, assigned, imported, or restored. No service was stopped.
This review changes only computer-local policy preparation and input validation;
router commands, radio behavior, package operations and device configuration are
outside its scope.

## Code round 1 - identification and relationships

New fault fixtures failed before fixes and passed afterwards:
- ActivePolicy containing a falsey value of the wrong registry type could be
  misclassified as no assigned policy. Check type before interpreting empty values.
- UUID parsing stripped incorrectly placed hyphens. One canonical GUID pattern is
  now shared by registry identification and command planning.
- Verification used case-sensitive object relationships although collision
  detection was case-insensitive. Case aliases could falsely reject the right
  object or miss an extra rule referencing the owned filter list. Relationship
  matching now uses the same case normalization and rejects ambiguous matches.
- Numeric string ports are length-bounded before conversion.

## Code round 2 - recovery evidence and read limits

Fault fixtures showed a backup could grow after the size check, or change size
while being read, and still become accepted recovery evidence. The reader now
actually requests at most BACKUP_LIMIT + 1 bytes and rejects changed/oversized data
before adding rules. Existing per-step failure, receipt failure, rollback, concurrent
change detection and protected-directory tests were re-run.

## Code round 3 - public service and diagnostic contracts

The existing GUI already validates ordinary login inputs, but the public service
preflight did not independently validate them before the new policy callback. The
worker now reuses the backend's pure validator before policy preparation and
normalizes the exact target forwarded to both policy and SSH. Cancellation is also
checked at the worker boundary. This is an entrypoint defense; it is not a claim
that invalid GUI inputs were observed changing a real computer policy.

A closed stdout pipe could raise BrokenPipeError from the new print in finally,
overriding an otherwise verified result. Removed the duplicate console print;
existing application logging and bounded ZIP evidence remain. Diagnostic enum
fields now reject unsupported types without a hashability exception. Successful
administrator responses must identify the requested address, port, route and a
known verification status instead of relying only on success=true. These response
and diagnostic cases are injected boundary tests, not observed corruption of the
existing authenticated administrator channel.

## Additional documentation round 1 - command and enforcement semantics

Compared native read-only netsh help with Microsoft command and IPsec references:
TCP/6, exact remote endpoint, source port 0, mirrored response traffic, permit,
connection type all, rule activation, and association with the existing policy.
The implementation does not assign a new policy or automatically force a reload.

Microsoft WFP arbitration and legacy troubleshooting distinguish stored policy
from effective filtering. Other filters/callouts can block a permitted flow. An
exact static dump does not prove runtime reachability; the ordinary SSH connection
and identity/authentication checks still follow. No guessing at raw-address aliases
or localized populated dump formats was added without a native sample.

## Additional documentation round 2 - native Windows API contracts

Reviewed registry types, GUID format, QueryServiceConfigW, security descriptors,
GetWindowsDirectoryW and GetSystemWindowsDirectoryW. Found a documented multi-user
compatibility difference: the former can return a private per-user Windows folder.
The privileged tool/backup root now uses GetSystemWindowsDirectoryW and requires
an absolute path. The documented drive-root form C: is normalized to C:\ before
joining System32 or Temp, so it never becomes a drive-relative executable path.
An injected multi-user fixture failed before the fix. Native directory, volume-ACL
capability and bounded netsh show-store probes passed read-only on this computer.

## Additional documentation round 3 - ownership, channel lifetime and recovery

Compared ShellExecuteEx/handle semantics, atomic replacement and the actual admin
host code. HostProcess.close releases its own handle and does not TerminateProcess.
The synchronous server finishes apply/rollback before attempting its response.
A real local IPC Pipe regression disconnects the client while an injected policy
transaction runs, then verifies that transaction completion precedes worker exit.
The operation itself is replaced with an in-memory fixture; no privileged mutation
or UAC prompt occurs in this test. Existing cancellation/timeouts never replay a
request automatically, and rollback never imports an old whole-machine policy.

## Final evidence and limits

137 targeted tests passed on Windows/Python 3.10.11, covering the previous policy,
normal firewall, authorization pipe, credentials, network readiness, login evidence
and feedback-export regressions plus the new boundary fixtures. Compilation,
UTF-8/replacement-character checks and git diff whitespace checks passed.

The source index is legacy-ipsec-review2-sources-20261104.json. Ten additional
Microsoft-owned/Python source snapshots were archived with byte counts and SHA256
under build/ipsec-review2-20261104/sources. URLs are moving documentation references;
the hashes identify the actual archived bytes, not a claimed pinned upstream commit.
Earlier command/API sources remain indexed by legacy-ipsec-review-sources-20261104.json.

Real disabled-firewall mutation, real populated localized dump formats, runtime
activation and actual affected-user SSH connectivity remain UNVERIFIED. The real
computer's network settings were not changed. Debian native execution was unavailable;
mocked platform isolation does not certify a Debian installation. No package built.
