# Aniki Community Packs

Discover and share community-made packs for **Aniki ReMake**.

Community Packs let you change the look and feel of Aniki ReMake without having to edit theme files manually. Packs are created with **Aniki Pack Creator** and are made to work with **Aniki Helper**.

## Pack types

- **Visual Packs** — change backgrounds and visual artwork across the theme.
- **Color Packs** — change the theme's color palette and overall style.
- **Login Packs** — replace the login screen video.
- **Sound Packs** — change interface sounds and theme music. Missing sounds automatically use the Aniki ReMake defaults.
- **Complete Packs** — apply several pack types together as one coordinated setup.

## Browse and install packs

The easiest way to use Community Packs is through **Aniki Helper**. Community packs can be browsed from the pack library and installed without manually extracting files into the theme.

Each pack shows its name, creator, version and preview when available.

## Share your own pack

Create your pack with **Aniki Pack Creator**, then use **Share Community Pack** inside the Creator. It prepares the exact file or files that need to be attached to the matching submission form in the **Issues** section of this repository.

Choose the submission form that matches your pack: **Visual, Color, Login, Sound or Complete**.

When submitting a pack:

1. Click **Share Community Pack** in Aniki Pack Creator.
2. Choose where the prepared Community upload file(s) should be created.
3. Open the matching submission form.
4. Attach the prepared ZIP file(s) and the requested preview image.
5. Wait for the automatic check and community review.

### Large packs and multipart uploads

GitHub limits individual ZIP attachments in Issues to **25 MB**. If a pack is too large, Aniki Pack Creator automatically splits the Community upload into several files such as:

```text
My Pack.part01-of-04.zip
My Pack.part02-of-04.zip
My Pack.part03-of-04.zip
My Pack.part04-of-04.zip
```

Attach **all generated parts** to the Pack ZIP field. Do not rename, extract or modify them.

The automatic validator verifies every part, reconstructs the original pack, checks its SHA-256 integrity, and then performs the normal pack validation. The final published GitHub Release still contains **one normal ZIP**; multipart files are only used to get the submission through GitHub Issues.

If the pack passes validation and is approved, it will be published automatically and become available through the Community Packs catalog.

## Updating one of your packs

Keep the original Aniki Pack Creator project after publishing a pack.

To release an update, open that same project, increase its version (for example `1.0.0` → `1.1.0`), use **Share Community Pack** again and submit it as an update. Reusing the same project keeps the permanent pack ID, which lets Aniki Helper recognize the new version correctly.


## Remove one of your published packs

If you no longer want one of your packs to appear in the Community catalog, open a **Remove / unpublish Community Pack** issue.

This is useful if, for example, you first published a standalone Visual Pack and later published a Complete Pack that replaces it.

You only need the permanent **Pack ID** of the exact pack you want removed. The automatic check confirms that the Pack ID exists, then a maintainer reviews the request. After approval:

- the pack is removed from `catalog.json`;
- its preview is removed from the repository;
- it disappears from the Community Packs browser in Aniki Helper;
- the existing GitHub Release is kept as an archive for safety and recovery.

The archived Release is not shown in the Community catalog and does not affect Aniki Helper.

## Community guidelines

Please only submit packs you are allowed to share. Packs may be rejected or removed if they are broken, misleading, unsafe, incompatible with Aniki ReMake, or create a rights issue.

Automatic validation checks the package itself, but publication still requires approval before a pack appears in the Community catalog.

---

**Aniki Community Packs** is made for the Aniki ReMake community. Create something you like, share it, and make it easy for other users to enjoy it too.
