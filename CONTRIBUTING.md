# Contributing a Visual Pack

Use the **Visual Pack submission** issue template instead of opening a pull request for a new pack.

Before submitting:

- Export the ZIP with a current version of Aniki Visual Pack Creator.
- Make sure all 14 required images are present.
- Keep the `.avpc` project used to create the pack. Its permanent ID is required for future updates.
- Use semantic versions such as `1.0.0`, `1.1.0` and `2.0.0`.
- Provide one representative preview image.
- Submit the ZIP exactly as exported by Aniki Visual Pack Creator. Do not add, remove or rename anything inside it.
- The ZIP must contain exactly `visualpack.json` plus the 14 required JPG files. GitHub validates this automatically; any additional, missing, duplicated or nested file makes the submission fail.

For an update, submit the newly exported ZIP from the same `.avpc` project and increase the version number.

## Automatic ZIP validation

When a Visual Pack submission issue is created or edited, GitHub Actions downloads the attached ZIP and validates it automatically. The check verifies the exact file list, flat archive structure, ZIP integrity, expected JPG dimensions, `visualpack.json`, permanent pack ID and semantic version.

A successful automatic check only means the package format is valid. Publication still requires review.
