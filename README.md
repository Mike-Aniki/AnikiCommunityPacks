# Aniki Community Visual Packs

Community catalog for Visual Packs created for **Aniki ReMake** with **Aniki Visual Pack Creator**.

The catalog is designed to be read directly by **Aniki Helper**, allowing community packs to be browsed and, later, installed or updated from inside Playnite.

## Submit a Visual Pack

1. Create or open your pack with Aniki Visual Pack Creator.
2. Fill in the pack name, author, version and optional description.
3. Export the completed Visual Pack ZIP.
4. Click **Share Community Pack** in the Creator, or open a new **Visual Pack submission** issue in this repository.
5. Attach the exported ZIP and one representative JPG or PNG preview image to the issue.

Every submitted ZIP is automatically checked by GitHub Actions. The archive must contain exactly `visualpack.json` and the 14 image files produced by Aniki Visual Pack Creator. Missing, renamed, duplicated, nested or additional files make the validation fail.

When validation succeeds, the issue is marked **ready-for-review**.

## Approval and automatic publication

Passing the automatic package check does **not** publish a pack by itself. A maintainer first reviews the submitted preview and the pack information.

If the submission is accepted, the maintainer adds the **approved** label. That single action starts the automatic publication workflow, which:

1. downloads and re-validates the submitted ZIP;
2. requires exactly one JPG or PNG preview attached to the issue;
3. checks whether the submission is a new pack or a valid newer version of an existing pack;
4. creates a GitHub Release and uploads the ZIP;
5. stores the preview in `previews/` using the permanent pack ID;
6. adds or updates the entry in `catalog.json`;
7. validates the updated catalog;
8. commits the catalog and preview to the default branch;
9. marks the issue **published** and closes it.

If any publication check fails, nothing is added to the catalog. The `approved` label is removed and the workflow comments with the reason so the submission can be fixed and approved again.

## Updating an existing pack

Open the original `.avpc` project, increase its version (for example `1.0.0` → `1.1.0`), export a new ZIP and submit it as **Update to an existing pack**.

Do **not** recreate the project just to publish an update. The `.avpc` file contains the permanent pack ID used to recognize future versions of the same pack.

For an update to publish automatically:

- the permanent ID must already exist in `catalog.json`;
- the submitted version must be newer than the currently published version;
- the same strict ZIP validation still applies.

## Catalog

The public catalog is stored in:

`catalog.json`

Raw catalog URL for Aniki Helper:

`https://raw.githubusercontent.com/Mike-Aniki/AnikiCommunityVisualPacks/main/catalog.json`

Each entry contains the permanent pack ID, name, author, version, description, preview URL, download URL and publication/update dates.

## Moderation

Community packs are reviewed before publication. GitHub validates the ZIP structure automatically, while the maintainer keeps the final editorial decision by applying the **approved** label.

A submission can still be rejected or a published pack can later be removed if it is broken, misleading, incompatible with Aniki ReMake, or creates a rights/safety issue.

Submitters are responsible for the files they provide. Content may be removed when necessary, including following a valid rights-holder request.

## Repository structure

```text
AnikiCommunityVisualPacks/
├── catalog.json
├── catalog.schema.json
├── previews/
├── scripts/
│   ├── prepare_publication.py
│   ├── validate_catalog.py
│   └── validate_submission.py
└── .github/
    ├── ISSUE_TEMPLATE/
    │   └── visual-pack-submission.yml
    └── workflows/
        ├── publish-approved.yml
        ├── setup-labels.yml
        ├── validate-catalog.yml
        └── validate-submission.yml
```

The Visual Pack ZIPs are hosted as GitHub Release assets rather than being committed directly into the repository.
