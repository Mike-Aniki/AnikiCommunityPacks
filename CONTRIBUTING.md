# Sharing a Community Pack

Use the matching **Community Pack submission** form in the Issues section. You do not need to open a pull request or edit `catalog.json` yourself.

Before submitting:

- Export the ZIP directly from **Aniki Pack Creator**.
- Keep the original Creator project so future updates keep the same permanent pack ID.
- Increase the version for every update (`1.0.0` → `1.1.0`, for example).
- Attach the exported ZIP without changing anything inside it.
- Attach one representative JPG or PNG preview image.
- Choose **New pack** for a first release or **Update to an existing pack** for a newer version of an already published pack.

## What happens after submission

GitHub automatically checks the ZIP for the selected pack type. Visual, Color, Login, Sound and Complete Packs each have their own validation rules. Complete Packs are also checked internally: every included sub-pack is validated before the submission can continue.

If the automatic check succeeds, the issue receives **ready-for-review**. A maintainer then reviews the submission.

If approved, the maintainer applies **approved**. GitHub automatically creates the Release, stores the preview, updates the Community Packs catalog, marks the issue as **published**, and closes it.

If validation fails, edit the issue after replacing or fixing the ZIP. The automatic check will run again.
