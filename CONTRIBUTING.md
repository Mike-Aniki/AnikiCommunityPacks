# Sharing a Community Pack

Use the matching **Community Pack submission** form in the Issues section. You do not need to open a pull request or edit `catalog.json` yourself.

Before submitting:
- Use **Share Community Pack** in **Aniki Pack Creator** to prepare the upload file(s).
- Keep the original Creator project so future updates keep the same permanent pack ID.
- Increase the version for every update (`1.0.0` → `1.1.0`, for example).
- Attach the prepared ZIP without changing anything inside it.
- If the Creator generates several `.partXX-of-XX.zip` files, attach **all of them** without renaming or modifying them.
- Choose the Community preview in Aniki Pack Creator before sharing. The Creator embeds it in the ZIP automatically; do not attach a separate preview image.
- Choose **New pack** for a first release or **Update to an existing pack** for a newer version of an already published pack.

## Large packs

GitHub limits individual ZIP attachments in Issues to **25 MB**. Aniki Pack Creator handles this automatically when you use **Share Community Pack**.

For a large pack, the Creator produces several multipart ZIPs. GitHub validates their metadata and SHA-256 hashes, reconstructs the original ZIP, and then runs the same validation as a normal single-file submission.

Multipart ZIPs are only submission files. If the pack is approved, the published GitHub Release contains one normal Community Pack ZIP.

## What happens after submission

GitHub first resolves the submission into one normal ZIP. A small submission is used directly; a multipart submission is verified and reconstructed automatically.

GitHub then checks the ZIP for the selected pack type. Visual, Color, Login, Sound and Complete Packs each have their own validation rules. Complete Packs are also checked internally: every included sub-pack is validated before the submission can continue.

If the automatic check succeeds, the issue receives **ready-for-review**. A maintainer then reviews the submission.
If approved, the maintainer applies **approved**. GitHub automatically creates the Release, stores the preview, updates the Community Packs catalog, marks the issue as **published**, and closes it.

If validation fails, replace or fix the submitted ZIP / multipart files and edit the issue. The automatic check will run again.


## Removing / unpublishing one of your packs

If you want a published pack removed from the Community catalog, use the **Remove / unpublish Community Pack** issue form.

Enter the permanent Pack ID for the exact pack you want removed and explain why. This is especially useful when a standalone pack has been replaced by a newer Complete Pack.

The bot checks that the Pack ID currently exists and marks a valid request as **ready-for-review**. A maintainer must verify the requester before applying **approved**.

After approval, GitHub automatically removes the pack from `catalog.json` and deletes its repository preview. The pack then disappears from Aniki Helper. Existing GitHub Releases are kept as archives so accidental removals can be recovered.

