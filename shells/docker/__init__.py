# Marks `shells.docker` as a package so `python -m shells.docker.entrypoint`
# resolves unambiguously (shells/ is a regular package). The Docker shell boots
# the shared `app` (daemon + web) headless; see entrypoint.py.
