# Aniki Community Visual Packs

Community catalog for Visual Packs created for **Aniki ReMake** with **Aniki Visual Pack Creator**.

The catalog is designed to be read directly by **Aniki Helper**, allowing community packs to be browsed and, later, installed or updated from inside Playnite.

## Submit a Visual Pack

1. Create or open your pack with Aniki Visual Pack Creator.
2. Fill in the pack name, author, version and optional description.
3. Export the completed Visual Pack ZIP.
4. Click **Share Community Pack** in the Creator, or open a new **Visual Pack submission** issue in this repository.
5. Attach the exported ZIP and one preview image to the issue.

Submissions are reviewed before being added to `catalog.json`.

## Updating an existing pack

Open the original `.avpc` project, increase its version (for example `1.0.0` → `1.1.0`), export a new ZIP and submit the update.

Do **not** recreate the project just to publish an update. The `.avpc` file contains the permanent pack ID used to recognize future versions of the same pack.

## Catalog

The public catalog is stored in:

`catalog.json`

Raw catalog URL for Aniki Helper:

`https://raw.githubusercontent.com/Mike-Aniki/AnikiCommunityVisualPacks/main/catalog.json`

Each entry contains the permanent pack ID, name, author, version, description, preview URL, download URL and publication/update dates.

## Moderation

Community packs are reviewed before publication. A submission can be rejected or removed if it is broken, malicious, misleading, incompatible with Aniki ReMake, or creates a rights/safety issue.

Submitters are responsible for the files they provide. Content may be removed when necessary, including following a valid rights-holder request.

## Repository structure

```text
AnikiCommunityVisualPacks/
├── catalog.json
├── catalog.schema.json
├── previews/
└── .github/
    └── ISSUE_TEMPLATE/
        └── visual-pack-submission.yml
```

The ZIP files do not need to live inside the Git repository. `catalog.json` only stores their download URLs, which keeps the catalog independent from the final hosting location.
