# agent-service pre-start hook. Sourced by infra/deployment/deploy-target.sh after the image is
# pulled and before the containers are (re)started. Available: the lib.sh helpers (step/ok/info/die/
# quiet) and $SRC $SVC_DIR $CFG $SHA $SHORT $IMAGE.
#
# What only agent-service needs:
#   - its data folders, owned by the users that write them: your ChatGPT login (uid 10001) and
#     PostgreSQL's data (uid 70, the image's postgres user)
#   - PostgreSQL's pinned image: the deploy starts containers with `--pull never`, and PostgreSQL
#     isn't built by CI, so it's pulled here (once; a no-op afterwards)

step "agent-service: data folders, PostgreSQL's image"

install -d -m 755 -o 0 -g 0 /var/lib/agent-service
install -d -m 700 -o 10001 -g 10001 /var/lib/agent-service/codex-home
install -d -m 700 -o 70 -g 70 /var/lib/agent-service/postgres
ok "/var/lib/agent-service: codex-home (10001), postgres (70)"

PG_IMAGE="$(docker compose config --images postgres)"
if docker image inspect "$PG_IMAGE" >/dev/null 2>&1; then
    ok "PostgreSQL image present: ${PG_IMAGE##*/}"
else
    quiet docker pull "$PG_IMAGE" || die "Cannot pull $PG_IMAGE"
    ok "PostgreSQL image pulled: ${PG_IMAGE##*/}"
fi
