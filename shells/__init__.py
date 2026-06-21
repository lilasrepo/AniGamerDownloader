# Marks `shells` as a package. The shells are thin launchers that boot the
# shared `app` (daemon + web) built on `core`; they keep gevent / pystray /
# pywebview / Docker concerns out of `core` and `app`.
