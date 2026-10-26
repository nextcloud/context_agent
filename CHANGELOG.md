<!--
  - SPDX-FileCopyrightText: 2024 Nextcloud GmbH and Nextcloud contributors
  - SPDX-License-Identifier: AGPL-3.0-or-later
-->
# Change Log
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](http://keepachangelog.com/)
and this project adheres to [Semantic Versioning](http://semver.org/).

## [2.10.0] - 2026-10-26

### Added
* feat: Implement dynamic impulse radius classification of tools
* feat: Implement destructiveness dimension to confirmation classification
* feat: support additional user instructions in the system prompt (Thanks to Viacheslav Huliaiev)

### Fixed
* fix: include OCS error response bodies in logged errors (Thanks to Cestercian)
* fix(agent): Load MemorySaver with our custom JsonPlusSerializer
* fix(agent): Use our custom JsonPlusSerializer in load_conversation_old instead of the one from langgraph
* fix(tools): extract absolute_url from the app_api OCS response in download links
* fix: ask for plain text when the reply is read out loud
* fix(web): Harden web fetch
* fix(search): name unified search tools after the provider id (Thanks to Volodymyr Fesko)
* fix(files): Make sure webdav failures raise an error
* fix(main): Prevent task polling from dying
* fix(agent): Raise recursion_limit to allow more tool calls per interaction
* perf(agent): memoize the user timezone lookup per user
* feat(agent): get_current_datetime tool, date-only system prompt
* fix(tools): emit DATE values for all-day events with next-day DTEND
* refactor(tools): default the timezone to the user's profile instead of requiring it
* fix(tools): preserve the event timezone as TZID instead of serializing to UTC
* fix(tools): schedule calendar events in the user's timezone, not the server's
* fix(tables): Small consistency fix
* fix(deck): More caching
* fix: dependency updates


## [2.9.1] - 2026-09-30

### Fixed
- Fixed build workflow
- fix(agent): Fix system prompt to verify old facts

## [2.9.0] - 2026-09-30

### Added
* feat(collectives): add Collectives support for page management (thanks to @Pavlinchen)
* feat: add advanced calendar event search (thanks to Dick Tump)
* feat: Add an nc link parser tool
* fix(mcp-server): Fix Server access through HaRP
* feat: Make taskprocessing timeouts trigger UserFacingError so the user knows what's up
* feat: Pass on userFacingErrorMessages from chatwithtools task providers

### Fixed
* fix: web_fetch tool
* fix: web_fetch redirect to internal file fetch for regex-matched links
* fix: disambiguate between nextcloud internal file links tool and web_fetch

## [2.8.0] - 2026-05-08

### Added
- support for NC35
- Assignment tools
- web_fetch tool
- Talk: reactions, replies, polls, and file sharing tools
- Stream output text and tool calls
- Extend context chat tool
- on-demand memory storage and recall tools
- support for multimodal chat inputs and outputs
- Tables app support
- tools for agent skills
- more files tools

### Changed
- Validate more inputs

### Fixed
- Reduce token usage by truncating old tool results
- don't lose message history due to history cut off

## [2.7.0] - 2026-07-15

### Added
- Deck: Add card discovery (list_board_cards) and card comment tools
- mail inbox tools

### Changed
- optimize tool call denied instruction

### Fixed
- MCP streamable-http transport for initializing

## [2.6.0] - 2026-04-16

### Added
- lots of more tools for
  - Bookmarks
  - Calendar
  - Circles
  - Cookbook
  - Deck
  - Files
  - Forms
  - Mail 
  - Shares
- "find user" tool
- Register and invoke async tools in MCP middleware

### Changed
- updated dependencies

### Fixed 
- Log tool error in handle_tool_error
- First history item cannot be a tool message


## [2.5.1] - 2026-03-10

### Changed
- replaced whitespaces in search tool names

## [2.5.0] - 2026-03-05

### Added
- Unified search provider tools
- Suggest tools for the user based on their interaction

### Changed 
- ny_py_api updated to 0.24.2


## [2.4.0] - 2026-02-26

### Added
- Make the app fully non-blocking

### Changed
- some prompt engineering to improve tool use


## [2.3.0] - 2025-12-29

### Added
- Support for NC34

### Changed
- updated python dependencies
- retry task scheduling when a connection error is thrown
- switch from httpx to niquests

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
