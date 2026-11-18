# speech-service pre-start hook. Sourced by infra/deployment/deploy-target.sh after the image is
# pulled and before the container is (re)started. Available: the lib.sh helpers (step/ok/info/die/
# quiet) and $SRC $SVC_DIR $CFG $SHA $SHORT $IMAGE.
#
# What only speech-service needs: the models' weights in /var/lib/speech-service/models, every file
# at its pinned revision and matching its SHA-256 (models.yaml). The new image does it (it has
# huggingface_hub): only what's missing or wrong is downloaded (~4.2 GB the first time), and nothing
# replaces a model until all of its files check out. No GPU needed.

step "speech-service: the models' weights, pinned and checked"

install -d -m 755 -o 0 -g 0 /var/lib/speech-service /var/lib/speech-service/models
docker run --rm --network host \
    -v "$SRC/infra/services/speech-service/models.yaml:/t/models.yaml:ro" \
    -v /var/lib/speech-service/models:/models \
    -e HF_HUB_DISABLE_TELEMETRY=1 --entrypoint python "$IMAGE" -m app.models fetch /t/models.yaml /models \
    || die "The models' weights could not be fetched or didn't match models.yaml (output above)."
ok "/var/lib/speech-service/models: $(ls /var/lib/speech-service/models | tr '\n' ' ')"
