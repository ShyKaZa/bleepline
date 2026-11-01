# Code signing (SignPath)

Unsigned downloads make Windows SmartScreen say *"Windows protected your PC"*. SignPath Foundation signs
open-source projects for free. The release workflow is ready for it: signing turns itself on as soon as the
two settings in step 3 exist, and until then releases are built unsigned exactly as before.

What gets signed: `Bleepline.exe`, the OBS plugin `bleepline-filter.dll` and `Bleepline-Setup.exe`. Never
third-party DLLs (SignPath's rules only allow signing your own code).

## 1. Apply (once)

Apply at https://signpath.org/apply with the repository https://github.com/ShyKaZa/bleepline.

Their conditions are at https://signpath.org/terms. Bleepline meets the main ones: it's MIT-licensed, released,
documented, has an uninstaller, built from source on GitHub-hosted runners, and does nothing on the network
unless asked to (downloads you start, and the opt-in update check). **One point to raise in the application,
honestly:** the speech engine is CTranslate2's official build, which has Intel's oneMKL compiled into it and
ships Intel's OpenMP runtime next to it. Both are freely redistributable but not open source (Intel Simplified
Software License; see THIRD_PARTY_NOTICES.txt). SignPath's terms ask for no proprietary components except system
libraries, so ask whether these are acceptable. (NVIDIA's cuDNN loader, which CTranslate2 also ships, is already
left out of the build.)

Requirements on your side: two-factor authentication on GitHub and on SignPath, and approving each release in
SignPath before it's signed.

## 2. Set up the project in SignPath (after approval)

- Project slug: `bleepline`
- Trusted build system: GitHub.com, linked to the project
- Signing policy slug: `release-signing` (approvals required)
- Two artifact configurations, pasted from this repository:
  - slug `app`: [.signpath/artifact-configurations/app.xml](.signpath/artifact-configurations/app.xml)
  - slug `setup`: [.signpath/artifact-configurations/setup.xml](.signpath/artifact-configurations/setup.xml)
- Create an API token for a user with the *submitter* role.

## 3. Connect GitHub

In the repository: Settings → Secrets and variables → Actions:

- Variable `SIGNPATH_ORGANIZATION_ID` = your SignPath organization ID
- Secret `SIGNPATH_API_TOKEN` = the API token

From then on, every tagged release sends two signing requests (the app files, then the installer built from
them). Approve each in SignPath; the workflow waits up to an hour for each approval.

## 4. Add the code signing policy to the README

SignPath requires this on the project page once signing is active. Replace the section "Code signing policy"
in README.md with:

> Free code signing provided by [SignPath.io](https://about.signpath.io), certificate by
> [SignPath Foundation](https://signpath.org).
>
> - Committers and reviewers: [Gorgho](https://github.com/ShyKaZa)
> - Approvers: [Gorgho](https://github.com/ShyKaZa)
>
> Privacy policy: this program will not transfer any information to other networked systems unless
> specifically requested by the user or the person installing or operating it.
