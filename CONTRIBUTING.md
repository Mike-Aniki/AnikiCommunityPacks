# Contributing a Visual Pack

Use the **Visual Pack submission** issue template instead of opening a pull request for a new pack.

Before submitting:

- Export the ZIP with a current version of Aniki Visual Pack Creator.
- Make sure all 14 required images are present.
- Keep the `.avpc` project used to create the pack. Its permanent ID is required for future updates.
- Use semantic versions such as `1.0.0`, `1.1.0` and `2.0.0`.
- Attach exactly one representative JPG or PNG preview image.
- Submit the ZIP exactly as exported by Aniki Visual Pack Creator. Do not add, remove or rename anything inside it.
- The ZIP must contain exactly `visualpack.json` plus the 14 required JPG files. GitHub validates this automatically; any additional, missing, duplicated or nested file makes the submission fail.

For an update, submit the newly exported ZIP from the same `.avpc` project, increase the version number, and select **Update to an existing pack** in the submission form.

## Automatic validation

When a Visual Pack submission issue is created or edited, GitHub Actions downloads the attached ZIP and validates it automatically. The check verifies the exact file list, flat archive structure, ZIP integrity, expected JPG dimensions, `visualpack.json`, permanent pack ID and semantic version.

A successful validation adds the **ready-for-review** label. A failed validation adds **validation-failed** and must be corrected before publication.

## Maintainer approval

Automatic validation only verifies the package format. A maintainer reviews the preview and decides whether the pack should be published.

If accepted, the maintainer applies the **approved** label. No manual Release or `catalog.json` edit is required after that.

The publication workflow re-validates the package and then automatically creates the Release, stores the preview, updates and validates `catalog.json`, commits the result, marks the issue **published**, and closes it.

If publication fails, the workflow removes **approved** and comments with the reason. Fix the issue and apply **approved** again when it is ready.
