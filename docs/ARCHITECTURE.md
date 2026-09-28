# DEEP33 Architecture

## Connectivity truth model

DEEP33 must never report ONLINE from local Wi-Fi or mobile-data state alone.

ONLINE requires evidence for:

1. Internet
2. Backend reachability
3. AI Gateway
4. Model availability
5. Successful chat path

Each layer has a separate status and failure mode.

## Provider isolation

Android talks only to DEEP33 Backend. Provider credentials remain server-side. The AI Gateway normalizes provider-specific behavior so the Android contract remains stable.

## Reuse policy

Historical IAC33/C-33/Andrew2.0 code may be reused only after inspection and verification. No historical secret, credential, environment file, or unverified provider configuration is imported.
