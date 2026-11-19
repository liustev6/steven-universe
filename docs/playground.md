# The playground (CT 110)

Where experiments run: spikes, dataset building, synthetic data on the GPU, model training. Production
containers keep only what production needs (CT 108 holds speech-service's image and pinned models, nothing
else), and the repository's services keep only their own code.

**Setup:** [lxc-gpu-passthrough-setup.md](lxc-gpu-passthrough-setup.md), Part 3. 12 cores, 32 GB, 500 GB,
the RTX 5060 shared with CT 108, `192.168.50.119`, SSH alias `playground` (through pve01), console
autologin, not started at boot. No DNS name: nothing serves from it, and SSH goes by IP (a DHCP reservation
keeps that stable). Add `playground.lan.liustev6.ca` only if it starts serving pages you open by name
(a notebook, TensorBoard), with a certificate, as the services have.

## Rules
- **GPU work stops only the production that shares its card.** LXC passthrough gives a card to any number
  of containers, so the playground can use any card, but VRAM isn't shared fairly: before GPU work here,
  stop the production services that use that card (today: speech-service on CT 108, ~7.7 of the RTX 5060's
  8 GB), by hand, and start them again when done (no automation: the playground is supervised work).
  Nothing else stops: services that call those (pi-server's voice turn) fail their turn cleanly meanwhile
  (voice-path.md V8) and recover when they're back. CPU work (features, most training) needs no window.
  With more cards, production gets its own and the playground another, and nothing needs stopping.

      ssh speech-host docker stop speech-service-speech-service-1          # before GPU work
      ssh speech-host docker start speech-service-speech-service-1         # after; then:
      curl -fs https://speech.lan.liustev6.ca/health                       # healthy in ~20 s (warm)
- **Code in git, data not.** Experiment code lives in `experiments/` in the repository; datasets, recordings,
  generated audio, features and models live on CT 110's disk (`/srv/experiments/<name>/`), never in git.
  Voice recordings are personal: they stay on this disk and the Mac.
- **Results are written down.** Each spike has a README with its question, setup, numbers and conclusion;
  a decision it leads to goes into the relevant spec, with the numbers inline.
- **Production takes only reviewed outputs.** A model reaches a service through the service's own pinned,
  reviewed path (e.g. pi-server's `models/`), never by a service reading the playground.
- **Downloaded models are pinned too:** each with a `REVISION.json` (repository and revision) next to it.

## Watching a run
Long jobs run in tmux on CT 110 (they survive a dropped SSH). Their logs, live in a browser:
**http://192.168.50.119:8080** (`experiments/logserver.py`: each run's readable log and latest raw output,
whether it's still running, the CPU), and TensorBoard's charts at **:6006** while a model trains.

## Layout
```
experiments/python/<name>/          the code: scripts, configs, phrase sets, spikes/NN-<topic>/ (README, results)
/srv/experiments/<name>/            on CT 110: data/, work/ (caches, features), models/, out/
```
