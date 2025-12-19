<!--
  - SPDX-FileCopyrightText: 2024 Nextcloud GmbH and Nextcloud contributors
  - SPDX-License-Identifier: AGPL-3.0-or-later
-->
# Change Log
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](http://keepachangelog.com/)
and this project adheres to [Semantic Versioning](http://semver.org/).

## [2.2.0] - 2025-12-19

### Added
- Implement taskprocessing trigger event
- Add "AI-generated" notes where appropriate
- Add optional memories input slot
- add AI app category for appstore


### Changed
- updated python dependencies
- Reduce size of conversation_token
- Impose a history limit to avoid the token intake exploding


## [2.1.0] - 2025-08-27

### Added
- Adding mcp servers as custom tools for context agent
- Expose an mcp server to use context agent's tools in other llms

### Fixed
- Tune prompt to not include tools in system prompt if they are disabled

## [2.0.0] - 2025-07-21

### Changed
- bumped min NC version to 31.0.8

### Fixed
- link generation for file outputs
- system prompt optimized

## [1.2.2] - 2025-06-26

### Fixed 
- fixed availability check for Calendar: it's now always available

## [1.2.1] - 2025-06-25

### Fixed
- fixed a bug that made Context Agent unusable for non-users
- made caching user-related
- adapted spelling in settings
- updated dependencies

## [1.2.0] - 2025-06-03

### Added
- image generation tool (AI)
- settings to dis/-able tools
- HaRP-Support
- added used tools to output so that Assistant can show them
- document generation tools: text documents, spreadsheets and slides
- Public transport tool using HERE API 
- Routing tool using OPenStreetMap
- get file content tool (Files)
- get folder tree tool (Files)
- create a public share link tool (Files)
- get mail accounts tool (Mail)
- Web search tool using DuckDuckGo
- OpenProject tools: list projects and create workpackage

### Changed
- use poetry instead of pip to manage dependencies
- Mail tool doesn't need the account ID anymore, it can be obtained by another tool
- calendar tools improved

### Fixed
- output when using Llama 3.1 fixed
- context chat tool fixed


## [1.1.0] - 2025-02-25

### Added
- create_conversations tool (Talk)
- search through addressbooks (Contacts)
- find_details_of_current_user tool (Contacts)
- find_free_time_slot_in_calendar tool (Calendar)
- transcribe_file tool (Files)
. add task tool (Calendar/Tasks)
- YouTube search tool
- add card tool (Deck)

- log statements for each tool

### Fixed
- Make it tell user about 3rd party network services
- add pythonpath to Dockerfile

## [1.0.4] - 2025-01-21

### Fixed

- fix: Fix error handling code

## [1.0.3] - 2025-01-21

### Fixed

- fix: ignore more temp exceptions during task polling


## [1.0.2] - 2025-01-21

### Fixed

 - fix: ignore temp exceptions during task polling

## [1.0.1] – 2025-01-20

### Fixed

- fix build

## [1.0.0] – 2025-01-20

Initial version
