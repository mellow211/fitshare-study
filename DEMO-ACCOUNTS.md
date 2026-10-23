# FitShare demo accounts

> Public demo credentials. Use only for this demo; do not reuse these passwords on real accounts.

| Role | Login ID | Password |
| --- | --- | --- |
| Teacher | `t1` | `111` |
| Student | `s1` | `111` |
| Student | `111` | `78N_YyUlAeeznyveqDUs` |
| Student | `222` | `MnVCKS0Nt4-KDnh9Pogf` |
| Student | `1` | `0jP5zpPm3DcWx5ECncRR` |

The server stores salted password hashes. This file contains the public demo passwords for logging in.

`t1` and `s1` are the accounts on the Vercel site (https://fitshare-study.vercel.app). `s1` (nickname 다솜) owns the example sharing records shown in the ranking. A local `python server.py` reads `server-data/accounts.json`, where `t1` still uses the old password and `s1` does not exist.
