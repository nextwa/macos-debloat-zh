#!/usr/bin/env python3
"""Manage macOS launchd services with a Chinese terminal interface.

Selections describe the requested configuration; runtime state is shown
separately. Presets stage changes, and application requires a preview.
"""
from __future__ import annotations

import curses
import contextlib
import glob
import io
import json
import os
import pwd
from datetime import datetime
import re
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

UID = int(os.environ.get("SUDO_UID", os.getuid()))
DOMAINS = ("system", f"gui/{UID}")
BACKUP_DIR = Path(pwd.getpwuid(UID).pw_dir) / "Library/Application Support/macos-debloat-zh"
PERSIST_LABEL = "io.github.nextwa.macos-debloat-zh"
PERSIST_PLIST = Path(f"/Library/LaunchDaemons/{PERSIST_LABEL}.plist")
PERSIST_DIR = Path("/Library/Application Support/macos-debloat-zh")
PERSIST_STATE = PERSIST_DIR / "persist.json"
PERSIST_REPORT = PERSIST_DIR / "last-run.json"
PERSIST_PASSES = 5
PERSIST_PASS_S = 60
PERSIST_WATCH_S = 300
PERSIST_REPORT_PASSES = 20

LAUNCHD_DIRS = (
    "/System/Library/LaunchDaemons",
    "/System/Library/LaunchAgents",
    "/System/Cryptexes/App/System/Library/LaunchDaemons",
    "/System/Cryptexes/App/System/Library/LaunchAgents",
    "/Library/Apple/System/Library/LaunchDaemons",
    "/Library/Apple/System/Library/LaunchAgents",
)

EMBEDDED_LABELS = """\
# Format: label  # comment (what breaks if disabled)
# Lines starting with `# ===` are section headers.

# === Spotlight / app launcher ===
# On 27, Siri AI.app (campo) hosts Cmd-Space and the four-finger Apps pinch.
# That is how you launch apps — not an AI extra. Not in balanced; --disable-all
# still reaches it if you have another launcher.
com.apple.campo                               # Cmd-Space + four-finger Apps pinch (Spotlight UI). Leave on to launch apps the usual way.

# === Siri / voice assistant [balanced] ===
com.apple.siriappintentsd                     # Siri App Intents orchestrator
com.apple.assistant_service                   # Siri execution / flow XPC
com.apple.assistantd [sip-off]                # Siri core
com.apple.Siri.agent                          # Siri agent
com.apple.SiriTTSTrainingAgent                # Siri voice training
com.apple.sirittsd [sip-off]                  # Siri TTS daemon
com.apple.siriinferenced [sip-off]            # on-device Siri inference
com.apple.siriknowledged [sip-off]            # Siri knowledge graph
com.apple.assistant_cdmd [sip-off]            # Siri continuous dialog manager
com.apple.parsecd [sip-off]                   # Siri/Spotlight suggestions backend
com.apple.parsec-fbf [sip-off]                # Siri Suggestions feedback
com.apple.intelligencecontextd                # Apple Intelligence context runtime
com.apple.intelligenceplatformd [sip-off]     # Apple Intelligence platform
com.apple.knowledge-agent [sip-off]           # knowledge graph extraction

# === Apple Intelligence (Tahoe) [balanced] ===
com.apple.mlruntimed                          # runtime for Apple's distributed (federated) ML model evaluation
com.apple.generativeexperiencesd [sip-off]    # Writing Tools / generative AI
com.apple.privatecloudcomputed [sip-off]      # Private Cloud Compute (AI cloud offload)
com.apple.modelcatalogd [sip-off]             # AI model catalog daemon
com.apple.ModelCatalogAgent [sip-off]         # AI model catalog agent
com.apple.modelmanagerd [sip-off]             # AI model manager / downloads
com.apple.naturallanguaged                    # NaturalLanguage framework daemon
com.apple.textunderstandingd                  # text understanding AI
com.apple.triald [sip-off]                    # Trial: Apple's A/B experiments and feature / asset rollouts (all apps, not only Siri)
com.apple.triald.system [sip-off]             # Trial, system side: experiments and asset rollouts for system daemons
com.apple.visualintelligenced                 # Visual Intelligence (screenshot / camera AI)
com.apple.GenerativeFunctions.agentstored     # Apple Intelligence agent session store
com.apple.contextstored [sip-off]             # context store (KNOWN >30GB MEMORY LEAK in Tahoe)

# === Telemetry / analytics [telemetry] ===
com.apple.analyticsd [sip-off]                # Apple diagnostics submission
com.apple.tipsd [sip-off]                     # Tips app notifications

# === AirDrop / Continuity (no iPhone) [balanced] ===
com.apple.sharingd [sip-off]                  # AirDrop / Handoff / share sheet (~60 MB resident)
com.apple.rapportd [sip-off]                  # device proximity / Continuity discovery

# === Diagnostics / crash reports [telemetry] ===
com.apple.ReportCrash                         # auto crash report generator
com.apple.spindump [sip-off]                  # hang detection / spin reports

# === Apple ads [telemetry] ===
com.apple.ap.adprivacyd [sip-off]             # ad privacy
com.apple.ap.promotedcontentd [sip-off]       # promoted apps

# === Proactive / predictive [balanced] ===
com.apple.coreduetd [sip-off]                 # activity tracker for proactive features
com.apple.duetexpertd [sip-off]               # prediction engine
com.apple.suggestd [sip-off]                  # Spotlight smart suggestions backend
com.apple.biomesyncd [sip-off]                # biome event sync
com.apple.navd                                # time-to-leave / commute predictions
com.apple.ospredictiond [sip-off]             # OS behavior predictions
com.apple.milod [sip-off]                     # MicroLocation: learns places and rooms of interest for location-based suggestions

# === Game Center / AirPlay receiver ===
com.apple.gamed                               # Game Center
com.apple.AirPlayXPCHelper [sip-off]          # AirPlay helper

# === Photos analysis ===
com.apple.photoanalysisd [sip-off]            # face/scene/object scan in Photos
com.apple.mediaanalysisd [sip-off]            # media library indexing

# === News / Stocks / Weather [balanced] ===
com.apple.weatherd [sip-off]                  # weather data daemon (menu bar / widgets)
com.apple.newsd                               # News app daemon

# === Apple ID nags / Family [balanced] ===
com.apple.familycircled [sip-off]             # Family Sharing
com.apple.followupd [sip-off]                 # Apple ID setup nags

# === Misc [balanced] ===
com.apple.helpd [sip-off]                     # Help viewer indexer
com.apple.ndoagent                            # AppleCare / new-device outreach nags

# === iMessage / FaceTime / phone relay (no iPhone) [balanced] ===
com.apple.imagent [sip-off]                   # iMessage agent
com.apple.telephonyutilities.callservicesd [sip-off] # phone call relay from iPhone
com.apple.imautomatichistorydeletionagent     # iMessage history cleanup
com.apple.imcore.imtransferagent [sip-off]    # iMessage attachments
com.apple.nearbyd [sip-off]                   # Continuity Nearby (AirDrop/Handoff discovery)
com.apple.callhistoryd                        # iPhone call history sync
com.apple.CallHistoryPluginHelper             # call history plugin
com.apple.CallHistorySyncHelper               # call history iCloud sync
com.apple.businessservicesd                   # Apple Business Chat

# === HomeKit ===
com.apple.homed [sip-off]                     # HomeKit daemon
com.apple.homeeventsd [sip-off]               # HomeKit events
com.apple.threadradiod                        # Thread radio (Matter/HomeKit)
com.apple.ThreadCommissionerService [sip-off] # Matter device commissioning

# === Apple Mail/Calendar/Contacts/Reminders (use Gmail/web instead) ===
com.apple.contactsd                           # Contacts sync
com.apple.calaccessd [sip-off]                # Calendar backend
com.apple.remindd [sip-off]                   # Reminders
com.apple.dataaccess.dataaccessd              # Exchange/CardDAV/CalDAV sync
com.apple.email.maild [sip-off]               # Mail daemon

# === Speech / dictation [balanced] ===
com.apple.speech.speechsynthesisd.arm64       # text-to-speech (native Apple Silicon)
com.apple.speech.speechsynthesisd.x86_64      # text-to-speech (Intel / Rosetta)
com.apple.speech.speechdatainstallerd         # voice data installer
com.apple.speechmaintenanced                  # on-device speech model maintenance

# === Wallpaper / thumbnails [balanced] ===
com.apple.quicklook.ThumbnailsAgent           # QuickLook thumbnails in Finder

# === App Store + update nags (keeps softwareupdated for security) [balanced] ===
com.apple.SoftwareUpdateNotificationManager [sip-off] # update nag popups
com.apple.amsengagementd [sip-off]            # Apple Media Services engagement

# === Misc dead weight [balanced] ===
com.apple.studentd                            # Schoolwork app
com.apple.linkd [sip-off]                     # App Intents runtime: app actions for Shortcuts, Siri and Spotlight — Shortcuts' app actions stop
com.apple.liveactivitiesd [sip-off]           # Live Activities widgets

# === iCloud (no iCloud use) ===
com.apple.cloudd [sip-off]                    # iCloud sync core
com.apple.cloudphotod [sip-off]               # iCloud Photos sync
com.apple.icloudwebd                          # iCloud.com / iCloud on the web

# === Location/prediction extras (Find My still intact) [balanced] ===
com.apple.routined [sip-off]                  # significant locations / location-aware reminders
com.apple.homeenergyd [sip-off]               # Home Energy widget

# === Time Machine ===
com.apple.backupd [sip-off]                   # Time Machine core backup daemon
com.apple.TMHelperAgent                       # Time Machine menu / reminders
com.apple.backupd-helper [sip-off]            # Time Machine scheduling helper

# === Focus / DND / Screen Time ===
com.apple.donotdisturbd [sip-off]             # Focus modes / Do Not Disturb
com.apple.ScreenTimeAgent [sip-off]           # Screen Time
com.apple.ScreenTimeSettingsAgent             # Screen Time settings UI agent
com.apple.ManagedSettingsAgent [sip-off]      # Screen Time app limits
com.apple.UsageTrackingAgent [sip-off]        # device usage tracking (Screen Time data)
com.apple.StatusKitAgent [sip-off]            # share Focus status (iMessage)

# === Apple Media Services / iPhone telephony / contacts [balanced] ===
com.apple.CommCenter [sip-off]                # iPhone cellular call relay (no iPhone)
com.apple.contacts.postersyncd                # Contacts profile picture sync

# === More AI/ML stragglers [balanced] ===
com.apple.mlhostd [sip-off]                   # on-device ML training host (Lighthouse)

# === Cosmetic / app daemons [balanced] ===
com.apple.powerchime                          # laptop charging chime sound
com.apple.talagent [sip-off]                  # app relaunch / window restore at login
com.apple.shazamd [sip-off]                   # Shazam music recognition (Control Center)

# === Safari (no Safari use) ===
com.apple.Safari.SafeBrowsing.Service [sip-off] # Safari Safe Browsing list updates
com.apple.SafariBookmarksSyncAgent [sip-off]  # Safari bookmarks iCloud sync
com.apple.SafariLaunchAgent                   # Safari startup agent

# === More AI / Apple Intelligence [balanced] ===
com.apple.siriactionsd [sip-off]              # Siri Shortcuts/Voice Shortcuts agent
com.apple.ContextStoreAgent [sip-off]         # CoreDuet ContextStoreAgent
com.apple.corespeechd [sip-off]               # Core Speech backbone (Siri/dictation)
com.apple.corespeechd_system [sip-off]        # Core Speech system variant
com.apple.speech.synthesisserver              # speech synthesis server
com.apple.spotlightknowledged                 # CoreSpotlight semantic search
com.apple.spotlightknowledged.importer        # CoreSpotlight importer
com.apple.spotlightknowledged.updater [sip-off] # CoreSpotlight updater
com.apple.callintelligenced                   # AI call analysis (call summaries)
com.apple.intelligenceflowd [sip-off]         # AI flow daemon
com.apple.intelligencetasksd                  # AI tasks daemon
com.apple.synapse.contentlinkingd             # Notes / Safari content links (backlinks)
com.apple.LinkedNotesUIService                # Notes backlink indicator UI (~26 MB)

# === Spotlight KeepAlive daemons (mdutil -d leaves these resident) [macos>=27] ===
# Not in telemetry/balanced. On Tahoe 26, launchctl on corespotlightd broke
# typed Cmd-Space. These rows are indexer KeepAlive after mdutil -d, not the
# overlay: on 27 Cmd-Space / the four-finger Apps pinch is com.apple.campo
# (Siri AI.app). Do not list com.apple.Spotlight here — that is the old
# overlay host, already feature-flagged off when campo is the UI.
com.apple.metadata.mds                        # Spotlight server (KeepAlive); off, new files are not indexed
com.apple.metadata.mds.index                  # Spotlight index helper; off, new files are not indexed
com.apple.metadata.mds.scan                   # Spotlight scan helper
com.apple.msrpc.mdssvc                        # Spotlight SMB/network metadata
com.apple.corespotlightd                      # CoreSpotlight daemon
com.apple.corespotlightservice                # CoreSpotlight service
com.apple.managedcorespotlightd               # managed CoreSpotlight
com.apple.metadata.mdbulkimport               # Spotlight ingest worker
com.apple.metadata.mdflagwriter               # Spotlight flag writer
com.apple.metadata.mdwrite                    # Spotlight metadata writer

# === Apple Music / iTunes / Media streaming ===
com.apple.itunescloudd [sip-off]              # Apple Music / iTunes Match cloud
com.apple.musicd                              # Apple Music local daemon
com.apple.amp.mediasharingd                   # Apple Music sharing
com.apple.mediastream.mstreamd                # iCloud media stream
com.apple.mediacontinuityd                    # media Continuity (no iPhone)
com.apple.mediaremoted [sip-off]              # media keys / now-playing
com.apple.mediaremoteagent [sip-off]          # media remote agent

# === Apple ID / Apple Pay / SSO ===
com.apple.appleaccountd [sip-off]             # Apple ID account daemon
com.apple.passd                               # Wallet / Apple Pay
com.apple.AppSSODaemon                        # Apple SSO Daemon
com.apple.financed [sip-off]                  # Wallet / Apple Card finance data
com.apple.adid [sip-off]                      # Apple Device Identity (CoreADI) — iCloud auth, not ads
com.apple.identityservicesd [sip-off]         # Apple ID identity (iMessage/FaceTime)
com.apple.AppSSOAgent                         # Apple SSO extensions
com.apple.cdpd [sip-off]                      # iCloud account recovery and keychain escrow (recovery contacts, iCloud Keychain)

# === App Store full kill (re-enable when needed) ===
com.apple.appstored                           # App Store daemon
com.apple.appstorecomponentsd                 # App Store components
com.apple.commerce [sip-off]                  # App Store commerce / purchases
com.apple.storeaccountd                       # App Store account
com.apple.storeassetd                         # App Store assets
com.apple.storedownloadd                      # App Store downloads
com.apple.storekitagent                       # StoreKit purchases
com.apple.storelegacy                         # legacy iTunes Store
com.apple.storereceiptinstaller               # app receipt installer
com.apple.storeuid                            # App Store UI daemon
com.apple.appplaceholdersyncd [sip-off]       # app placeholder sync
com.apple.appstoreagent [sip-off]             # App Store agent
com.apple.amsaccountsd [sip-off]              # Apple Media Services account state
com.apple.amsondevicestoraged [sip-off]       # App Store on-device storage scan

# === iCloud user-facing notifications [balanced] ===
com.apple.iCloudNotificationAgent [sip-off]   # iCloud notification UI
com.apple.iCloudUserNotificationsd            # iCloud user notifications
com.apple.icloudmailagent                     # iCloud Mail (no Apple Mail)
com.apple.FollowUpUI                          # Apple ID nag UI

# === Apple Books (no Books use) ===
com.apple.bookassetd                          # Apple Books asset downloads
com.apple.bookdatastored                      # Apple Books data store

# === Telemetry extras [telemetry] ===
com.apple.geoanalyticsd [sip-off]             # Geo telemetry
com.apple.SubmitDiagInfo [sip-off]            # diagnostic submitter to Apple
com.apple.BiomeAgent [sip-off]                # biome event hub agent
com.apple.biomed [sip-off]                    # biome event hub daemon

# === Contacts/Photos extras ===
com.apple.contacts.donation-agent [sip-off]   # Contacts → Siri Suggestions feed
com.apple.photolibraryd [sip-off]             # Photos library daemon

# === Filesystem / cosmetic [balanced] ===
com.apple.filesystems.fskitd                  # File System Kit core (3rd-party FS plugins)
com.apple.fskit.fskit_agent                   # FSKit agent
com.apple.fskit.fskit_helper                  # FSKit helper
com.apple.chronod [sip-off]                   # widget host (widgets gone, demand-loaded by NC)

# === Contacts / AddressBook (no Mac Contacts use) ===
com.apple.AddressBook.AssistantService        # Contacts assistant
com.apple.AddressBook.SourceSync              # Contacts source sync
com.apple.AddressBook.abd                     # Contacts daemon
com.apple.peopled                             # People: contact-based person actions (FaceTime, Calendar people)

# === Audio route suggestions ===
com.apple.intelligentroutingd                 # suggests audio / AirPlay output routes (one-tap route picker) — not Maps

# === Maps (Google Maps user) ===
com.apple.Maps.mapssyncd [sip-off]            # Apple Maps sync
com.apple.Maps.mapspushd                      # Apple Maps push
com.apple.maps.destinationd                   # Apple Maps destinations

# === Beta program enrollment (not on macOS beta) [telemetry] ===
com.apple.appleseed.fbahelperd                # AppleSeed feedback assistant helper
com.apple.appleseed.seedusaged                # AppleSeed usage telemetry
com.apple.appleseed.seedusaged.postinstall    # AppleSeed postinstall
com.apple.betaenrollmentagent                 # beta enrollment agent
com.apple.betaenrollmentd                     # beta enrollment daemon
com.apple.feedbackd [sip-off]                 # Feedback Assistant daemon

# === Game controllers (no gamepad attached) ===
com.apple.GameController.gamecontrolleragentd # game controller agent
com.apple.GameController.gamecontrollerd      # game controller daemon
com.apple.GamePolicyAgent [sip-off]           # game policy agent
com.apple.gamepolicyd [sip-off]               # game policy daemon
com.apple.gamesaved                           # game saves

# === Sidecar / iPad second display (no iPad) [balanced] ===
com.apple.sidecar-display-agent               # Sidecar display agent
com.apple.sidecar-relay                       # Sidecar relay

# === Continuity Capture (no iPhone webcam) [balanced] ===
com.apple.cmio.ContinuityCaptureAgent [sip-off] # iPhone as webcam agent
com.apple.companiond                          # Apple Watch / iPhone companion

# === Avatar / Memoji / Stickers (no iMessage) [balanced] ===
com.apple.avatarsd                            # Memoji / avatars
com.apple.stickersd                           # iMessage stickers

# === Safari extras (no Safari + no iCloud Keychain) ===
com.apple.Safari.History                      # Safari history
com.apple.Safari.PasswordBreachAgent          # password leak checker (needs iCloud Keychain)
com.apple.SafariHistoryServiceAgent           # Safari history XPC
com.apple.SafariNotificationAgent             # Safari web notifications

# === Family / Parental controls (no family setup) [balanced] ===
com.apple.familycontrols                      # family controls daemon
com.apple.familycontrols.useragent            # family controls user agent
com.apple.FamilyControlsAgent                 # family controls agent
com.apple.familynotificationd                 # family notifications
com.apple.parentalcontrols.check              # parental controls checker
com.apple.askpermissiond                      # family purchase approval (Ask to Buy)
com.apple.assessmentagent [sip-off]           # education assessment mode
com.apple.progressd                           # ClassKit education progress

# === Proactive / Siri-adjacent (no Siri) [balanced] ===
com.apple.proactived                          # proactive engine
com.apple.proactiveeventtrackerd              # proactive event tracker
com.apple.knowledgeconstructiond              # knowledge graph construction
com.apple.reversetemplated                    # reverse template AI

# === AirPlay sender UI [balanced] ===
com.apple.AirPlayUIAgent [sip-off]            # AirPlay sender UI (sending to Apple TV)

# === Sports / News extras [balanced] ===
com.apple.sportsd                             # Apple Sports data

# === Apple FairPlay DRM (no DRM-protected Apple content) ===
com.apple.fairplayd                           # FairPlay DRM
com.apple.fairplaydeviceidentityd             # FairPlay device identity

# === Diagnostics extras (all telemetry to Apple) [telemetry] ===
com.apple.diagnosticd                         # diagnostics
com.apple.diagnosticextensionsd [sip-off]     # diagnostic extensions
com.apple.diagnostics_agent [sip-off]         # diagnostics agent
com.apple.diagnosticservicesd                 # diagnostic services
com.apple.diagnosticspushd                    # diagnostic push
com.apple.metrickitd                          # MetricKit (app metrics to Apple)
com.apple.osanalytics.osanalyticshelper [sip-off] # OS analytics helper
com.apple.spindump_agent                      # spindump agent (process hangs)
com.apple.metadata.mds.spindump               # Spotlight spindump diagnostic
com.apple.diagnosticextensions.osx.spotlight.helper  # Spotlight diagnostic extension
com.apple.audioanalyticsd [sip-off]           # audio analytics
com.apple.inputanalyticsd [sip-off]           # input analytics (typing/touch)
com.apple.ecosystemanalyticsd                 # ecosystem analytics
com.apple.analyticsagent [sip-off]            # analytics agent
com.apple.CrashReporterSupportHelper [sip-off] # crash reporter support
com.apple.DiagnosticsReporter                 # diagnostics reporter
com.apple.InstallerDiagnostics.installerdiagd # installer diagnostics
com.apple.InstallerDiagnostics.installerdiagwatcher  # installer diag watcher
com.apple.loginwindow.LWWeeklyMessageTracer   # weekly login telemetry
com.apple.symptomsd-diag [sip-off]            # symptoms diag (keep core symptomsd)
com.apple.symptomsd.distributed-agent         # symptoms distributed agent
com.apple.rtcreportingd [sip-off]             # FaceTime/WebRTC call quality reports
com.apple.securityuploadd                     # security telemetry upload
com.apple.usbctelemetryd                      # USB-C port telemetry
com.apple.systemstats.analysis [sip-off]      # system stats analysis
com.apple.systemstats.daily                   # daily system stats
com.apple.systemstats.microstackshot_periodic # periodic microstackshots
com.apple.signpost.signpost_reporter          # signpost perf telemetry
com.apple.enhancedloggingd                    # MDM enhanced logging

# === Software update extras (keep softwareupdated + swcd) [balanced] ===
com.apple.softwareupdate_firstrun_tasks       # one-shot first-boot SU tasks

# === Misc Apple legacy / unused [balanced] ===
com.apple.AOSPushRelay                        # legacy iCloud push relay
com.apple.nfcd [sip-off]                      # NFC daemon (no Apple Pay)
com.apple.facetimemessagestored               # FaceTime message store
com.apple.calendar.CalendarAgentBookmarkMigrationService  # one-shot Cal bookmark migration

# === Apple ID auth (kill — re-enable when needed for App Store) ===
com.apple.akd [sip-off]                       # AuthKit (Apple ID) — re-enable when logging into App Store

# === Apple Translate / Notes / VoiceOver / accessibility extras [balanced] ===
com.apple.translationd                        # Apple Translate API (Translate app, Safari translate)
com.apple.notes.exchangenotesd                # Apple Notes Exchange sync
com.apple.VoiceOver                           # screen reader (no accessibility use)
com.apple.accessibility.LiveTranscriptionAgent  # live transcription/captions
com.apple.accessibility.axassetsd [sip-off]   # TTS voice + VoiceOver/Magnifier model asset downloads
com.apple.DictationIM                         # dictation input method
com.apple.voicebankingd [sip-off]             # custom voice creation (Live Speech)
com.apple.voicememod                          # Voice Memos app

# === iCloud settings / language assets ===
com.apple.cloudsettingssyncagent              # iCloud settings sync
com.apple.languageassetd                      # Apple language model assets (Siri/dictation/translate)
com.apple.erasecontentsettingshelperd         # Erase All Content (only used for factory reset)

# === Apple TV watchlist / social / WiFi telemetry [balanced] ===
com.apple.watchlistd                          # Apple TV+ watchlist
com.apple.sociallayerd [sip-off]              # Game Center social layer
com.apple.wifianalyticsd [sip-off]            # WiFi telemetry

# === T2 / bridgeOS ===
# Same labels on T2 Intel and Apple Silicon. Disabling them breaks the
# macOS Update install step on both. Not in balanced.
com.apple.bridgeOSUpdateProxy                 # firmware/update proxy (T2 and Apple Silicon)
com.apple.bosreporter                         # bridgeOS install reporting
com.apple.boswatcher                          # bridgeOS install watcher (macOS Update installs)

# === Battery / power logging (charge limiting needs these) ===
com.apple.perfpowermetricd                    # perf/power metrics; off was reported to break charge limiting (macOS limiter, AlDente)
com.apple.powerlogHelperd                     # battery usage logging (Settings battery graph); off was reported to break charge limiting

# === Print (no printer) ===
com.apple.printtool.agent                     # print tool agent
com.apple.printtool.daemon                    # print tool daemon
com.apple.printuitool.agent                   # print UI tool agent

# === Xcode / iOS dev stack (FE/BE dev, no mobile) [balanced] ===
com.apple.previewsd                           # Xcode SwiftUI previews
com.apple.dt.AutomationModeUI                 # Xcode automation UI
com.apple.dt.automationmode-writer            # Xcode automation writer

# === Apple Music Player (AMP) suite — no Apple Music ===
com.apple.AMPArtworkAgent                     # album artwork
com.apple.AMPDeviceDiscoveryAgent [sip-off]   # Apple Music device discovery (not AirPods BT)
com.apple.AMPDevicesAgent                     # Apple Music devices agent
com.apple.AMPLibraryAgent                     # Apple Music library
com.apple.AMPSystemPlayerAgent                # system music player agent

# === Apple TV+ / video subscriptions ===
com.apple.videosubscriptionsd                 # Apple TV+ subscriptions

# === Touch Bar (M4 has none) [balanced] ===
com.apple.nowplayingtouchui                   # Touch Bar now-playing UI
com.apple.touchbarserver                      # Touch Bar server
com.apple.controlstrip                        # Touch Bar control strip
com.apple.SpacesTouchBarAgent.app             # Touch Bar Spaces agent
com.apple.accessibility.dfrhud                # Touch Bar accessibility HUD


# === Safari Web Inspector / WebKit push (no Safari) ===
com.apple.webinspectord                       # Safari Web Inspector
com.apple.webkit.webpushd                     # WebKit push (Safari web notifications)


# === iCloud Drive / Keychain Circle / SyncedDefaults / CloudDocs ===
com.apple.bird [sip-off]                      # iCloud Drive sync daemon
com.apple.security.keychain-circle-notification  # iCloud Keychain notification
com.apple.syncdefaultsd [sip-off]             # iCloud synced defaults (app prefs)

# === MDM / Managed apps (no MDM enrollment) [balanced] ===
com.apple.managedappdistributionagent [sip-off] # managed app distribution agent
com.apple.managedappdistributiond [sip-off]   # managed app distribution daemon


# === Paired-device sync (Replicator) ===
com.apple.replicatord [sip-off]               # syncs data with your paired devices (used by the widget host) — not Time Machine


# === 🧠 Apple Intelligence / AI 全系 (macOS 15+) ===
com.apple.intelligenceplatform  # 🧠 Apple Intelligence / AI 全系 (macOS 15+)；仅处理本机实际存在的服务
com.apple.intelligencecontext  # 🧠 Apple Intelligence / AI 全系 (macOS 15+)；仅处理本机实际存在的服务
com.apple.WritingTools  # 🧠 Apple Intelligence / AI 全系 (macOS 15+)；仅处理本机实际存在的服务
com.apple.writingtoolsd  # 🧠 Apple Intelligence / AI 全系 (macOS 15+)；仅处理本机实际存在的服务
com.apple.ImagePlayground  # 🧠 Apple Intelligence / AI 全系 (macOS 15+)；仅处理本机实际存在的服务
com.apple.imageplaygroundd  # 🧠 Apple Intelligence / AI 全系 (macOS 15+)；仅处理本机实际存在的服务
com.apple.Genmoji  # 🧠 Apple Intelligence / AI 全系 (macOS 15+)；仅处理本机实际存在的服务
com.apple.genmojid  # 🧠 Apple Intelligence / AI 全系 (macOS 15+)；仅处理本机实际存在的服务
com.apple.VisualIntelligence  # 🧠 Apple Intelligence / AI 全系 (macOS 15+)；仅处理本机实际存在的服务
com.apple.mlhost  # 🧠 Apple Intelligence / AI 全系 (macOS 15+)；仅处理本机实际存在的服务
com.apple.aiml  # 🧠 Apple Intelligence / AI 全系 (macOS 15+)；仅处理本机实际存在的服务
com.apple.aimld  # 🧠 Apple Intelligence / AI 全系 (macOS 15+)；仅处理本机实际存在的服务
com.apple.appintents  # 🧠 Apple Intelligence / AI 全系 (macOS 15+)；仅处理本机实际存在的服务
com.apple.appintentsd  # 🧠 Apple Intelligence / AI 全系 (macOS 15+)；仅处理本机实际存在的服务
com.apple.ondeviceintelligence  # 🧠 Apple Intelligence / AI 全系 (macOS 15+)；仅处理本机实际存在的服务
com.apple.semanticindex  # 🧠 Apple Intelligence / AI 全系 (macOS 15+)；仅处理本机实际存在的服务
com.apple.semanticindexd  # 🧠 Apple Intelligence / AI 全系 (macOS 15+)；仅处理本机实际存在的服务
com.apple.contextengine  # 🧠 Apple Intelligence / AI 全系 (macOS 15+)；仅处理本机实际存在的服务
com.apple.contextengined  # 🧠 Apple Intelligence / AI 全系 (macOS 15+)；仅处理本机实际存在的服务
com.apple.personalsemanticunderstanding  # 🧠 Apple Intelligence / AI 全系 (macOS 15+)；仅处理本机实际存在的服务
com.apple.privacypreservingml  # 🧠 Apple Intelligence / AI 全系 (macOS 15+)；仅处理本机实际存在的服务
com.apple.federatedlearning  # 🧠 Apple Intelligence / AI 全系 (macOS 15+)；仅处理本机实际存在的服务
com.apple.neuralengine  # 🧠 Apple Intelligence / AI 全系 (macOS 15+)；仅处理本机实际存在的服务
com.apple.neuralengined  # 🧠 Apple Intelligence / AI 全系 (macOS 15+)；仅处理本机实际存在的服务
com.apple.aimodeld  # 🧠 Apple Intelligence / AI 全系 (macOS 15+)；仅处理本机实际存在的服务
com.apple.generativeexperiences  # 🧠 Apple Intelligence / AI 全系 (macOS 15+)；仅处理本机实际存在的服务

# === 🎙️ Siri 全系 ===
com.apple.Siri  # 🎙️ Siri 全系；仅处理本机实际存在的服务
com.apple.siri.embeddedspeech  # 🎙️ Siri 全系；仅处理本机实际存在的服务
com.apple.siri-intelligence-platform  # 🎙️ Siri 全系；仅处理本机实际存在的服务
com.apple.siri.context  # 🎙️ Siri 全系；仅处理本机实际存在的服务
com.apple.knowledge-construction  # 🎙️ Siri 全系；仅处理本机实际存在的服务
com.apple.sirianalyticsd  # 🎙️ Siri 全系；仅处理本机实际存在的服务

# === 📱 iPhone Mirroring (macOS 15+) ===
com.apple.iPhoneMirroring  # 📱 iPhone Mirroring (macOS 15+)；仅处理本机实际存在的服务
com.apple.iphonemirroringd  # 📱 iPhone Mirroring (macOS 15+)；仅处理本机实际存在的服务
com.apple.continuitydisplay  # 📱 iPhone Mirroring (macOS 15+)；仅处理本机实际存在的服务

# === 📷 照片 & 媒体分析 ===
com.apple.media-stream-client  # 📷 照片 & 媒体分析；仅处理本机实际存在的服务

# === 📍 位置服务 ===
com.apple.geod  # 📍 位置服务；仅处理本机实际存在的服务
com.apple.locationmenu  # 📍 位置服务；仅处理本机实际存在的服务
com.apple.CoreLocationAgent  # 📍 位置服务；仅处理本机实际存在的服务

# === 🗺️ 地图 & 天气 ===
com.apple.Maps.pushdaemon  # 🗺️ 地图 & 天气；仅处理本机实际存在的服务

# === 📰 新闻 & 股票 & 体育 ===
com.apple.newsinternetd  # 📰 新闻 & 股票 & 体育；仅处理本机实际存在的服务
com.apple.stocks  # 📰 新闻 & 股票 & 体育；仅处理本机实际存在的服务
com.apple.sports  # 📰 新闻 & 股票 & 体育；仅处理本机实际存在的服务

# === ☁️ iCloud ===
com.apple.nsurlsessiond  # ☁️ iCloud；仅处理本机实际存在的服务
com.apple.nsurlstoraged  # ☁️ iCloud；仅处理本机实际存在的服务

# === 💬 iMessage / FaceTime ===
com.apple.imtransferagent  # 💬 iMessage / FaceTime；仅处理本机实际存在的服务
com.apple.imdpersistenceagent  # 💬 iMessage / FaceTime；仅处理本机实际存在的服务
com.apple.avconferenced  # 💬 iMessage / FaceTime；仅处理本机实际存在的服务

# === 🌐 Safari 后台 ===
com.apple.SafariCloudHistoryPushAgent  # 🌐 Safari 后台；仅处理本机实际存在的服务
com.apple.WebKit.Networking  # 🌐 Safari 后台；仅处理本机实际存在的服务
com.apple.WebKit.WebContent  # 🌐 Safari 后台；仅处理本机实际存在的服务
com.apple.Safari.SandboxBroker  # 🌐 Safari 后台；仅处理本机实际存在的服务

# === 🎵 Apple Music ===
com.apple.iTunesHelper  # 🎵 Apple Music；仅处理本机实际存在的服务
com.apple.iTunes  # 🎵 Apple Music；仅处理本机实际存在的服务

# === 📡 AirDrop / Handoff ===
com.apple.airplayd  # 📡 AirDrop / Handoff；仅处理本机实际存在的服务
com.apple.sidecar  # 📡 AirDrop / Handoff；仅处理本机实际存在的服务

# === 🔍 查找我的 ===
com.apple.findmy  # 🔍 查找我的；仅处理本机实际存在的服务
com.apple.icloud.findmydeviced  # 🔍 查找我的；仅处理本机实际存在的服务
com.apple.icloud.fmfd  # 🔍 查找我的；仅处理本机实际存在的服务

# === 📝 备忘 & 提醒 & 日记 ===
com.apple.Notes  # 📝 备忘 & 提醒 & 日记；仅处理本机实际存在的服务
com.apple.NotesMigrationService  # 📝 备忘 & 提醒 & 日记；仅处理本机实际存在的服务
com.apple.journald  # 📝 备忘 & 提醒 & 日记；仅处理本机实际存在的服务
com.apple.Journal  # 📝 备忘 & 提醒 & 日记；仅处理本机实际存在的服务

# === 🛒 App Store 后台 ===
com.apple.storebookkeeperd  # 🛒 App Store 后台；仅处理本机实际存在的服务
com.apple.appstore  # 🛒 App Store 后台；仅处理本机实际存在的服务
com.apple.softwareupdate_notify_agent  # 🛒 App Store 后台；仅处理本机实际存在的服务

# === 🖼️ Quick Look ===
com.apple.quicklook.ui.helper  # 🖼️ Quick Look；仅处理本机实际存在的服务
com.apple.quicklook.QLThumbnail  # 🖼️ Quick Look；仅处理本机实际存在的服务

# === 🐛 崩溃报告 ===
com.apple.ReportGPURestart  # 🐛 崩溃报告；仅处理本机实际存在的服务
com.apple.DiagnosticReportCleanup  # 🐛 崩溃报告；仅处理本机实际存在的服务
com.apple.ProblemReporter  # 🐛 崩溃报告；仅处理本机实际存在的服务

# === ⌨️ 语音 & 听写 ===
com.apple.speech.speechsynthesisd  # ⌨️ 语音 & 听写；仅处理本机实际存在的服务
com.apple.TextInputMenuAgent  # ⌨️ 语音 & 听写；仅处理本机实际存在的服务
com.apple.Dictation  # ⌨️ 语音 & 听写；仅处理本机实际存在的服务
com.apple.speech.voicebankingd  # ⌨️ 语音 & 听写；仅处理本机实际存在的服务

# === 📇 通讯录/日历/邮件 ===
com.apple.CalendarAgent  # 📇 通讯录/日历/邮件；仅处理本机实际存在的服务
com.apple.Calendar  # 📇 通讯录/日历/邮件；仅处理本机实际存在的服务
com.apple.mail  # 📇 通讯录/日历/邮件；仅处理本机实际存在的服务
com.apple.MailServiceAgent  # 📇 通讯录/日历/邮件；仅处理本机实际存在的服务
com.apple.MailCacheDelete  # 📇 通讯录/日历/邮件；仅处理本机实际存在的服务

# === 🔑 密码 App (macOS 15+) ===
com.apple.Passwords  # 🔑 密码 App (macOS 15+)；仅处理本机实际存在的服务
com.apple.passwordsd  # 🔑 密码 App (macOS 15+)；仅处理本机实际存在的服务

# === 👨‍👩‍👧 家长控制 ===
com.apple.parentalcontrols  # 👨‍👩‍👧 家长控制；仅处理本机实际存在的服务
com.apple.screentime  # 👨‍👩‍👧 家长控制；仅处理本机实际存在的服务

# === 🔧 其他无用服务 ===
com.apple.unmountassistant  # 🔧 其他无用服务；仅处理本机实际存在的服务
com.apple.wifi.WiFiAgent  # 🔧 其他无用服务；仅处理本机实际存在的服务
com.apple.AOSHeartbeat  # 🔧 其他无用服务；仅处理本机实际存在的服务
com.apple.watchdogd  # 🔧 其他无用服务；仅处理本机实际存在的服务
com.apple.Accessibility  # 🔧 其他无用服务；仅处理本机实际存在的服务
com.apple.universalaccessd  # 🔧 其他无用服务；仅处理本机实际存在的服务
com.apple.universalaccesscontrol  # 🔧 其他无用服务；仅处理本机实际存在的服务
com.apple.accessibility  # 🔧 其他无用服务；仅处理本机实际存在的服务
com.apple.ZoomWindow  # 🔧 其他无用服务；仅处理本机实际存在的服务
com.apple.beacond  # 🔧 其他无用服务；仅处理本机实际存在的服务
com.apple.proximitycontrol  # 🔧 其他无用服务；仅处理本机实际存在的服务
com.apple.wifip2pd  # 🔧 其他无用服务；仅处理本机实际存在的服务
com.apple.wifivelocityd  # 🔧 其他无用服务；仅处理本机实际存在的服务
com.apple.TrustEvaluationAgent  # 🔧 其他无用服务；仅处理本机实际存在的服务
com.apple.automatedDeviceEnrollment  # 🔧 其他无用服务；仅处理本机实际存在的服务
com.apple.invitesd  # 🔧 其他无用服务；仅处理本机实际存在的服务
com.apple.freeformd  # 🔧 其他无用服务；仅处理本机实际存在的服务
com.apple.healthd  # 🔧 其他无用服务；仅处理本机实际存在的服务
com.apple.Health  # 🔧 其他无用服务；仅处理本机实际存在的服务
com.apple.transparencyd  # 🔧 其他无用服务；仅处理本机实际存在的服务
com.apple.ESEServiceAgent  # 🔧 其他无用服务；仅处理本机实际存在的服务
com.apple.MobileSoftwareUpdate  # 🔧 其他无用服务；仅处理本机实际存在的服务
com.apple.OTATaskingAgent  # 🔧 其他无用服务；仅处理本机实际存在的服务
com.apple.backgroundassets.user  # 🔧 其他无用服务；仅处理本机实际存在的服务
com.apple.periodic-  # 🔧 其他无用服务；仅处理本机实际存在的服务
com.apple.weekly-  # 🔧 其他无用服务；仅处理本机实际存在的服务
com.apple.monthly-  # 🔧 其他无用服务；仅处理本机实际存在的服务
com.apple.daily-  # 🔧 其他无用服务；仅处理本机实际存在的服务
"""

USER_LABELS_FILE = BACKUP_DIR / "labels.txt"
# Embedded + USER_LABELS_FILE, counted by load_sections before the version and
# absent-label drops remove rows. The TUI header cannot recount it afterwards.
CATALOG_TOTAL = 0
PRESETS_DIR = BACKUP_DIR / "presets"

# AirDrop uses Wi-Fi, Bluetooth and account/contact identity. Application
# search keeps the Spotlight UI, local index and LaunchServices.
EXTREME_PRESET = "extreme-keep-airdrop-search"
AIR_DROP_KEEP = {
    "com.apple.sharingd", "com.apple.rapportd", "com.apple.nearbyd",
    "com.apple.wifip2pd", "com.apple.wifi.WiFiAgent", "com.apple.bluetoothd",
    "com.apple.bluetoothuserd", "com.apple.bluetoothUIServer", "com.apple.airportd",
    "com.apple.identityservicesd", "com.apple.akd", "com.apple.accountsd",
    "com.apple.contactsd", "com.apple.AddressBook.abd",
    "com.apple.AddressBook.AssistantService", "com.apple.Siri.agent",
}
SEARCH_KEEP = {"com.apple.campo", "com.apple.Spotlight", "com.apple.lsd"}
SEARCH_PREFIXES = ("com.apple.metadata.", "com.apple.mdworker",
                   "com.apple.spotlight", "com.apple.corespotlight")
CORE_KEEP = {
    "com.apple.WindowServer", "com.apple.dock", "com.apple.Finder",
    "com.apple.systemuiserver", "com.apple.loginwindow", "com.apple.controlcenter",
    "com.apple.notificationcenterui", "com.apple.securityd", "com.apple.authd",
    "com.apple.trustd", "com.apple.tccd", "com.apple.syspolicyd",
    "com.apple.mDNSResponder", "com.apple.networkd", "com.apple.configd",
    "com.apple.coreaudiod", "com.apple.audio.coreaudiod", "com.apple.bluetoothaudiod",
    "com.apple.powerd", "com.apple.displaypolicyd", "com.apple.thermalmonitord",
    "com.apple.watchdogd", "com.apple.coreservicesd", "com.apple.cfprefsd",
    "com.apple.distnoted", "com.apple.pboard", "com.apple.timed",
    "com.apple.universalaccessd", "com.apple.universalaccesscontrol",
    "com.apple.TextInputMenuAgent", "com.apple.TrustEvaluationAgent",
}


def preserve_reason(label: str) -> str:
    if label in AIR_DROP_KEEP:
        return "AirDrop 及联系人身份识别"
    if label in SEARCH_KEEP or label.startswith(SEARCH_PREFIXES):
        return "应用搜索与本地索引"
    if label in CORE_KEEP:
        return "系统基础服务"
    return ""


BUILTIN_PRESETS = {
    EXTREME_PRESET: (),
    "telemetry": ("telemetry",),
    "balanced": ("telemetry", "balanced"),
}


@dataclass
class Item:
    label: str
    comment: str
    section: str
    disabled: bool = False
    selected: bool | None = False
    domains: set[str] = field(default_factory=set)
    action: str = ""
    state: str = ""
    is_sip_off_required: bool = False
    is_locked: bool = False
    disabled_domains: set[str] = field(default_factory=set)
    pids: list[int] = field(default_factory=list)


@dataclass
class Section:
    title: str
    preset: str = ""
    macos: str = ""
    items: list[Item] = field(default_factory=list)


def macos_matches(spec: str, major: int | None) -> bool:
    """Whether this macOS major version satisfies a catalog gate like '>=27'."""
    if not spec or major is None:
        return True
    s = spec[5:] if spec.startswith("macos") else spec
    m = re.match(r"^(>=|<=|==|>|<)(\d+)$", s)
    if not m:
        return True
    op, n = m.group(1), int(m.group(2))
    return {">=": major >= n, "<=": major <= n, "==": major == n,
            ">": major > n, "<": major < n}[op]


def parse_section_header(raw_title: str) -> Section:
    """Trailing [balanced] / [macos>=27] tags; remainder is the section title."""
    preset = ""
    macos = ""
    title = raw_title
    while True:
        tag = re.search(r"\s*\[([^\]]+)\]\s*$", title)
        if not tag:
            break
        token = tag.group(1)
        title = title[:tag.start()].rstrip()
        if token in BUILTIN_PRESETS:
            preset = token
        elif token.startswith("macos"):
            macos = token[len("macos"):]
        elif re.match(r"^(>=|<=|==|>|<)\d+$", token):
            macos = token
    return Section(title=title, preset=preset, macos=macos)


SIP_OFF_TAG = "[sip-off]"


def parse_labels(source: Path | str) -> list[Section]:
    text = source.read_text() if isinstance(source, Path) else source
    sections: list[Section] = []
    current = Section(title="(uncategorized)")
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        m = re.match(r"^#\s*===\s*(.+?)\s*===\s*$", line)
        if m:
            if current.items:
                sections.append(current)
            current = parse_section_header(m.group(1))
            continue
        if line.lstrip().startswith("#"):
            continue
        if "#" in line:
            label_part, comment = line.split("#", 1)
            label = label_part.strip()
            comment = comment.strip()
        else:
            label = line.strip()
            comment = ""
        is_sip_off_required = label.endswith(SIP_OFF_TAG)
        if is_sip_off_required:
            label = label[:-len(SIP_OFF_TAG)].strip()
        if label:
            current.items.append(Item(label=label, comment=comment, section=current.title,
                                      is_sip_off_required=is_sip_off_required))
    if current.items:
        sections.append(current)
    return sections


@dataclass(frozen=True)
class CatalogStats:
    """Sizes of a parsed catalog. Always counted from labels, never a literal."""
    total: int
    sections: int
    telemetry: int
    balanced: int

    @property
    def beyond_balanced(self) -> int:
        return self.total - self.balanced


def catalog_stats(sections: list[Section] | None = None) -> CatalogStats:
    """Count labels and preset sizes from `EMBEDDED_LABELS` (or `sections`).

    This is the only catalog-size API. Help, status, the TUI and docs sync
    all read it so adding a label cannot leave a stale 270 sitting somewhere.
    """
    if sections is None:
        sections = parse_labels(EMBEDDED_LABELS)
    total = telemetry = balanced = 0
    nsec = 0
    for sec in sections:
        n = len(sec.items)
        if not n:
            continue
        nsec += 1
        total += n
        if sec.preset == "telemetry":
            telemetry += n
            balanced += n
        elif sec.preset == "balanced":
            balanced += n
    return CatalogStats(total=total, sections=nsec,
                        telemetry=telemetry, balanced=balanced)


def plist_domains() -> dict[str, set[str]]:
    """label -> launchd domains its job definition lives in. A LaunchAgents
    plist is a `gui/$UID` job, a LaunchDaemons plist a `system` job."""
    found: dict[str, set[str]] = {}
    for d in LAUNCHD_DIRS:
        domain = f"gui/{UID}" if d.endswith("LaunchAgents") else "system"
        for p in glob.glob(d + "/*.plist"):
            found.setdefault(os.path.basename(p)[:-6], set()).add(domain)
    return found


def domain_state(domain: str) -> tuple[dict[str, int], set[str]]:
    """({label: pid, 0 when not running}, labels disabled by an override there).
    One `launchctl print <domain>` carries both; its `disabled services` block
    is the same table `print-disabled` shows, and its `services` block is
    launchd's own pid for every job registered in the domain."""
    r = subprocess.run(["launchctl", "print", domain],
                       capture_output=True, text=True, check=False)
    registered: dict[str, int] = {}
    disabled: set[str] = set()
    block = ""
    for line in r.stdout.splitlines():
        header = re.match(r"^\t([\w ]+) = \{$", line)
        if header:
            block = header.group(1)
            continue
        if line.startswith("\t}"):
            block = ""
            continue
        if block == "services":
            fields = line.split()
            if fields:
                registered[fields[-1]] = int(fields[0]) if fields[0].isdigit() else 0
        elif block == "disabled services":
            entry = re.match(r'\s*"([^"]+)"\s*=>\s*(true|disabled)\s*$', line)
            if entry:
                disabled.add(entry.group(1))
    return registered, disabled


def drop_wrong_macos(sections: list[Section],
                     major: int | None = None) -> list[str]:
    """Drop sections gated to another macOS major (e.g. [macos>=27] on 26)."""
    if major is None:
        major = macos_major()
    skipped: list[str] = []
    keep_secs: list[Section] = []
    for sec in sections:
        if sec.macos and not macos_matches(sec.macos, major):
            skipped.extend(it.label for it in sec.items)
            continue
        keep_secs.append(sec)
    sections[:] = keep_secs
    return skipped


def drop_absent_labels(sections: list[Section]) -> list[str]:
    """Record for every label the domains it lives in, and remove the ones this
    macOS build doesn't have at all (renamed/removed by Apple, or wrong for this
    OS). Returns the dropped labels so callers can report them. Prevents phantom
    entries showing as enabled no-ops."""
    from_plists = plist_domains()
    registered = {d: domain_state(d)[0] for d in DOMAINS}
    absent: list[str] = []
    for sec in sections:
        keep: list[Item] = []
        for it in sec.items:
            it.domains = set(from_plists.get(it.label, ()))
            it.domains |= {d for d in DOMAINS if it.label in registered[d]}
            if it.domains:
                keep.append(it)
            else:
                absent.append(it.label)
        sec.items = keep
    sections[:] = [s for s in sections if s.items]
    return absent


def refresh_state(sections: list[Section]) -> None:
    """An override only takes effect in the domain the job is registered in, so
    a label counts as disabled only when every domain it lives in overrides it.
    Unioning the domains instead reports an inert override as success."""
    states = {d: domain_state(d) for d in DOMAINS}
    for sec in sections:
        for it in sec.items:
            it.disabled_domains = {d for d in it.domains if it.label in states[d][1]}
            it.disabled = bool(it.domains) and it.disabled_domains == it.domains
            it.pids = sorted({states[d][0].get(it.label, 0) for d in it.domains} - {0})
            it.selected = None if it.disabled_domains and not it.disabled else not it.disabled


def service_status(item: Item) -> str:
    if item.disabled:
        return "disabled-running" if item.pids else "disabled-stopped"
    if item.disabled_domains:
        return "partial-disabled"
    return "enabled-running" if item.pids else "enabled-idle"


STATUS_TEXT = {
    "disabled-running": "禁用但仍运行",
    "disabled-stopped": "已禁用·未运行",
    "partial-disabled": "部分域仍禁用",
    "enabled-running": "允许·运行中",
    "enabled-idle": "允许·按需启动",
}


def service_record(item: Item) -> dict:
    return {"label": item.label, "domains": sorted(item.domains),
            "disabled_domains": sorted(item.disabled_domains), "disabled": item.disabled,
            "pids": item.pids, "status": service_status(item),
            "keep_reason": preserve_reason(item.label), "sip_tag": item.is_sip_off_required}


def running_pids(labels: list[str]) -> dict[str, list[int]]:
    """{label: [pids]} for the given labels, straight from launchd. Matching a
    process name to a label instead guesses wrong whenever two labels share a
    last segment (`com.apple.spindump` / `com.apple.metadata.mds.spindump`)."""
    if not labels:
        return {}
    wanted = set(labels)
    out: dict[str, list[int]] = {}
    for domain in DOMAINS:
        for label, pid in domain_state(domain)[0].items():
            if pid and label in wanted:
                out.setdefault(label, []).append(pid)
    return out


def pending_changes(sections: list[Section]) -> tuple[list[str], list[str]]:
    to_disable: list[str] = []
    to_enable: list[str] = []
    for sec in sections:
        for it in sec.items:
            wants_enabled = it.selected
            if wants_enabled is None:
                continue
            if wants_enabled and (it.disabled or it.disabled_domains):
                to_enable.append(it.label)
            elif not wants_enabled and not it.disabled:
                to_disable.append(it.label)
    return to_disable, to_enable


def bootstrap_if_absent(label: str, domain: str) -> str | None:
    """Re-register an enabled service that an earlier bootout removed."""
    if label in domain_state(domain)[0]:
        return
    for directory in LAUNCHD_DIRS:
        is_daemon = directory.endswith("LaunchDaemons")
        if is_daemon != (domain == "system"):
            continue
        plist = Path(directory) / f"{label}.plist"
        if plist.exists():
            result = subprocess.run(["sudo", "launchctl", "bootstrap", domain, str(plist)],
                                    capture_output=True, text=True, check=False)
            return result.stderr.strip() or f"退出码 {result.returncode}" if result.returncode else None


def progress(text: str) -> None:
    """Overwrite one line in place. An apply is two sudo `launchctl` runs per
    label per domain, so `--disable-all` sits silent for hundreds of them
    otherwise. Skipped off a tty so piped output never gets \\r spam."""
    if not sys.stdout.isatty():
        return
    # Wrapping would leave every frame on screen instead of overwriting one line.
    w = min(78, os.get_terminal_size().columns - 1)
    sys.stdout.write("\r" + text[:w].ljust(w) + "\r")
    sys.stdout.flush()


def apply_changes(sections: list[Section], report=progress, *, retry_running: bool = False,
                  stop_processes: bool = True) -> dict:
    to_disable, to_enable = pending_changes(sections)
    if retry_running:
        desired = [it.label for sec in sections for it in sec.items if it.selected is False and not it.is_locked]
        to_disable = sorted(set(to_disable) | set(running_pids(desired)))
    domains_of = {it.label: it.domains for sec in sections for it in sec.items}
    override_failures: list[tuple[str, str]] = []
    bootout_failures: dict[str, int] = {}
    bootout_errors: list[dict] = []
    bootstrap_errors: list[dict] = []

    def launchctl(action: str, domain: str, label: str) -> None:
        r = subprocess.run(["sudo", "launchctl", action, f"{domain}/{label}"],
                           capture_output=True, text=True, check=False)
        if r.returncode == 0:
            return
        said = (r.stderr or r.stdout).strip().splitlines()
        msg = said[-1] if said else f"exit {r.returncode}"
        if action == "bootout":
            if r.returncode == 3:
                return
            bootout_failures[msg] = bootout_failures.get(msg, 0) + 1
            bootout_errors.append({"label": label, "domain": domain,
                                   "code": r.returncode, "message": msg,
                                   "sip_blocked": r.returncode == 150 or "System Integrity Protection" in msg})
        else:
            override_failures.append((f"{action} {domain}/{label}", msg))

    # Phase 1: launchctl disable + bootout (or enable), only in the domains the
    # job is registered in — an override written elsewhere never takes effect.
    total = len(to_disable) + len(to_enable)
    width = len(str(total))
    for n, label in enumerate(to_disable, 1):
        report(f"  禁用/停止  {n:>{width}}/{total}  {label}")
        for domain in sorted(domains_of[label]):
            launchctl("disable", domain, label)
            launchctl("bootout", domain, label)
    for n, label in enumerate(to_enable, len(to_disable) + 1):
        report(f"  恢复启用   {n:>{width}}/{total}  {label}")
        for domain in sorted(domains_of[label]):
            launchctl("enable", domain, label)
            error = bootstrap_if_absent(label, domain)
            if error:
                bootstrap_errors.append({"label": label, "domain": domain, "message": error})

    # Stop remaining processes once; report anything launchd starts again.
    killed = 0
    if to_disable and stop_processes:
        report("  检查仍运行的目标…")
        survivors = running_pids(to_disable)
        for label, pids in survivors.items():
            report(f"  终止进程   {label}")
            for pid in pids:
                r = subprocess.run(["sudo", "kill", "-9", str(pid)],
                                   capture_output=True, check=False)
                if r.returncode == 0:
                    killed += 1

    # Phase 3: brief settle then verify nothing respawned
    report("  核对配置与实际运行状态…")
    time.sleep(0.4)
    stragglers: dict[str, list[int]] = {}
    if to_disable:
        stragglers = running_pids(to_disable)

    disabled_now = {d: domain_state(d)[1] for d in DOMAINS}
    report("")
    return {
        "disabled": len(to_disable),
        "enabled": len(to_enable),
        "killed": killed,
        "stragglers": stragglers,
        "override_failures": override_failures,
        "bootout_failures": bootout_failures,
        "bootout_errors": bootout_errors,
        "bootstrap_errors": bootstrap_errors,
        "sip_blocked": sorted({row["label"] for row in bootout_errors if row["sip_blocked"]}),
        "stopped": sorted(label for label in to_disable if label not in stragglers
                          and all(label in disabled_now[d] for d in domains_of[label])),
        "not_disabled": [l for l in to_disable
                         if not all(l in disabled_now[d] for d in domains_of[l])],
        "not_enabled": [l for l in to_enable
                        if any(l in disabled_now[d] for d in domains_of[l])],
    }


def apply_problems(result: dict) -> str:
    """Format the same per-service failures for the CLI, dialog and saved report."""
    out: list[str] = []
    for cmd, msg in result["override_failures"]:
        out.append(f"  配置失败  launchctl {cmd}: {msg}")
    for row in result.get("bootout_errors", []):
        reason = "SIP 拒绝卸载" if row["sip_blocked"] else "卸载失败"
        out.append(f"  {reason}  {row['domain']}/{row['label']}: {row['message']}")
    if "bootout_errors" not in result:
        for message, count in result.get("bootout_failures", {}).items():
            out.append(f"  卸载失败 {count} 次：{message}")
    for row in result.get("bootstrap_errors", []):
        out.append(f"  重新注册失败  {row['domain']}/{row['label']}: {row['message']}")
    for key, verb in (("not_disabled", "禁用"), ("not_enabled", "启用")):
        if result[key]:
            out.append(f"{len(result[key])} 项未在所有所属域完成{verb}：")
            out.extend(f"  {label}" for label in result[key])
    if result["stragglers"]:
        out.append(f"{len(result['stragglers'])} 项仍在运行；禁用标记不等于进程停止：")
        out.extend(f"  {label}  pids={pids}" for label, pids in result["stragglers"].items())
    if result.get("persist_error"):
        out.append(f"开机保持未完成：{result['persist_error']}")
    if result.get("spotlight_error"):
        out.append(f"Spotlight 未完成：{result['spotlight_error']}")
    return "".join(f"{line}\n" for line in out)


def apply_incomplete(result: dict) -> bool:
    return bool(result["not_disabled"] or result["not_enabled"] or result["stragglers"]
                or result.get("bootout_errors") or result.get("bootstrap_errors")
                or result.get("persist_error") or result.get("spotlight_error"))


def apply_summary(result: dict) -> str:
    stopped = f"{len(result['stopped'])} 项" if "stopped" in result else "未记录"
    blocked = f"{len(result['sip_blocked'])} 项" if "sip_blocked" in result else "未逐项记录"
    return (f"尝试禁用/停止 {result['disabled']} 项；已禁用且未运行 {stopped}；"
            f"仍运行 {len(result['stragglers'])} 项；SIP 拒绝卸载 {blocked}；"
            f"尝试启用 {result['enabled']} 项，未启用 {len(result['not_enabled'])} 项")


def reopen_tty_stdin() -> bool:
    """`curl ... | python3` feeds the script on stdin, leaving fd 0 as a spent
    pipe rather than a terminal. curses then busy-loops on getch (ERR, spawning
    mdutil every frame) or fails cbreak outright. Point fd 0 at the controlling
    terminal so the TUI reads real keystrokes."""
    if sys.stdin.isatty():
        return True
    try:
        tty = open("/dev/tty")
    except OSError:
        return False
    os.dup2(tty.fileno(), 0)
    sys.stdin = tty
    return True


def is_sip_enabled() -> bool:
    r = subprocess.run(["csrutil", "status"], capture_output=True, text=True, check=False)
    return "disabled" not in r.stdout


def lock_sip_rows(sections: list[Section], is_sip_on: bool) -> int:
    """With SIP on, a `[sip-off]` label is restarted by launchd within seconds of
    a disable, so it can't be switched off — only back on. Returns the count."""
    locked = 0
    for sec in sections:
        for it in sec.items:
            it.is_locked = is_sip_on and it.is_sip_off_required
            locked += it.is_locked
    return locked


def keep_locked_on(sections: list[Section]) -> int:
    """Undo any selection that would switch a locked label off."""
    kept = 0
    for sec in sections:
        for it in sec.items:
            if it.is_locked and it.selected is False and not it.disabled:
                it.selected = True
                kept += 1
    return kept


SIP_COST = "关闭 SIP 会降低系统保护，并可能影响 iPhone/iPad 应用等功能"


def recovery_steps(is_on: bool) -> str:
    """Apple silicon only lowers boot security from Recovery entered with the
    power button, so a physical Mac can refuse csrutil from macOS."""
    return f"""需要在恢复环境调整 SIP，完成后重启：
  Apple Silicon：关机后长按电源键，选择“选项”并继续。
  Intel：重新启动时按住 Command-R。
  在恢复环境的“实用工具 > 终端”中运行：csrutil {'enable' if is_on else 'disable'}
  完成认证并重新启动，再执行脚本。关闭 SIP 会降低系统保护。
"""


def set_sip(is_on: bool) -> int:
    """`csrutil` asks y/n, an admin user and their password on the terminal."""
    r = subprocess.run(["sudo", "csrutil", "enable" if is_on else "disable"], check=False)
    return r.returncode


def prime_sudo() -> bool:
    result = subprocess.run(["sudo", "-v"], check=False)
    return result.returncode == 0


def spotlight_state() -> str:
    """'on', 'off', or 'indexing' (index wiped, rebuild in progress).

    Read from `/`, not the Data volume: after `mdutil -d` the Data volume
    answers "unknown indexing state" while `/` says "Indexing and searching
    disabled", so reading Data reports a disabled Spotlight as rebuilding."""
    r = subprocess.run(["mdutil", "-s", "/"], capture_output=True, text=True, check=False)
    if "disabled" in r.stdout:
        return "off"
    if "Indexing enabled" in r.stdout:
        return "on"
    return "indexing"


SPOTLIGHT_SECTION = "Spotlight 文件索引"
SPOTLIGHT_COMMENT = "为 Finder、文件搜索和启动器提供索引；关闭会停止文件索引与搜索，保留应用搜索时应开启。"
SPOTLIGHT_INDEXING = "索引状态待确认或重建中，搜索结果可能暂不完整；重建期间会占用 CPU。"


def spotlight_section() -> Section:
    item = Item(label="Spotlight", comment=SPOTLIGHT_COMMENT, section=SPOTLIGHT_SECTION)
    section = Section(title=SPOTLIGHT_SECTION, items=[item])
    refresh_spotlight(section)
    return section


def refresh_spotlight(section: Section) -> None:
    item = section.items[0]
    item.state = spotlight_state()
    item.disabled = item.state == "off"
    item.selected = not item.disabled
    item.comment = SPOTLIGHT_INDEXING if item.state == "indexing" else SPOTLIGHT_COMMENT


def spotlight_set(enabled: bool, report=progress) -> tuple[bool, str]:
    report(f"  {'开启' if enabled else '关闭'} Spotlight 索引…")
    if not enabled:
        # `-i off` leaves the Data volume's indexer running on macOS 26; `-d`
        # stops indexing and searching on every volume.
        r = subprocess.run(["sudo", "mdutil", "-a", "-d"],
                           capture_output=True, text=True, check=False)
        report("")
        if r.returncode != 0:
            return False, f"mdutil failed: {r.stderr.strip()}"
        return True, "Spotlight 已关闭：文件索引与搜索已停止"
    r = subprocess.run(["sudo", "mdutil", "-a", "-i", "on"],
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        report("")
        return False, f"mdutil failed: {r.stderr.strip()}"
    # `-i on` only sets the flag; a previously disabled index stays in an
    # "unknown" state and never rebuilds, so Finder metadata queries hang.
    e = subprocess.run(["sudo", "mdutil", "-a", "-E"],
                       capture_output=True, text=True, check=False)
    report("")
    if e.returncode != 0:
        return False, f"mdutil -E failed: {e.stderr.strip()}"
    return True, "Spotlight 已开启：正在重建索引，搜索结果可能暂不完整"


def flat_index(sections: list[Section]) -> list[Item]:
    out: list[Item] = []
    for sec in sections:
        for it in sec.items:
            out.append(it)
    return out


MENU_SECTION = "操作与预设"
FILTERS = ("全部", "禁用仍运行", "保留功能", "待更改")
SECTION_TITLES = {
    "Spotlight / app launcher": "应用搜索与启动",
    "Siri / voice assistant": "Siri 语音助手",
    "Apple Intelligence (Tahoe)": "Apple Intelligence 智能功能",
    "Telemetry / analytics": "使用统计与分析",
    "AirDrop / Continuity (no iPhone)": "AirDrop 与接力互通",
    "Diagnostics / crash reports": "诊断与崩溃报告",
    "Apple ads": "Apple 广告",
    "Proactive / predictive": "主动建议与预测",
    "Game Center / AirPlay receiver": "游戏中心与 AirPlay 接收",
    "Photos analysis": "照片分析",
    "News / Stocks / Weather": "新闻、股票与天气",
    "Apple ID nags / Family": "Apple 账号提醒与家庭",
    "Misc": "其他后台功能",
    "iMessage / FaceTime / phone relay (no iPhone)": "信息、FaceTime 与电话接力",
    "HomeKit": "家庭与智能家居",
    "Apple Mail/Calendar/Contacts/Reminders (use Gmail/web instead)": "邮件、日历、通讯录与提醒",
    "Speech / dictation": "语音与听写",
    "Wallpaper / thumbnails": "墙纸与缩略图",
    "App Store + update nags (keeps softwareupdated for security)": "App Store 与更新提醒",
    "Misc dead weight": "其他可选后台服务",
    "iCloud (no iCloud use)": "iCloud 同步",
    "Location/prediction extras (Find My still intact)": "位置与预测辅助",
    "Time Machine": "时间机器备份",
    "Focus / DND / Screen Time": "专注模式与屏幕使用时间",
    "Apple Media Services / iPhone telephony / contacts": "媒体服务、电话与联系人",
    "More AI/ML stragglers": "其他 AI 与机器学习服务",
    "Cosmetic / app daemons": "应用外观与后台服务",
    "Safari (no Safari use)": "Safari 浏览器",
    "More AI / Apple Intelligence": "其他 Apple Intelligence 服务",
    "Spotlight KeepAlive daemons (mdutil -d leaves these resident)": "Spotlight 常驻服务",
    "Apple Music / iTunes / Media streaming": "音乐、iTunes 与媒体流",
    "Apple ID / Apple Pay / SSO": "Apple 账号、支付与单点登录",
    "App Store full kill (re-enable when needed)": "App Store 完整服务",
    "iCloud user-facing notifications": "iCloud 通知",
    "Apple Books (no Books use)": "Apple 图书",
    "Telemetry extras": "其他统计与分析服务",
    "Contacts/Photos extras": "联系人与照片辅助服务",
    "Filesystem / cosmetic": "文件系统与外观辅助",
    "Contacts / AddressBook (no Mac Contacts use)": "通讯录与地址簿",
    "Audio route suggestions": "音频设备建议",
    "Maps (Google Maps user)": "Apple 地图",
    "Beta program enrollment (not on macOS beta)": "测试版计划",
    "Game controllers (no gamepad attached)": "游戏控制器",
    "Sidecar / iPad second display (no iPad)": "随航与 iPad 扩展屏幕",
    "Continuity Capture (no iPhone webcam)": "连续互通相机",
    "Avatar / Memoji / Stickers (no iMessage)": "头像、拟我表情与贴纸",
    "Safari extras (no Safari + no iCloud Keychain)": "Safari 与钥匙串辅助",
    "Family / Parental controls (no family setup)": "家庭与家长控制",
    "Proactive / Siri-adjacent (no Siri)": "Siri 建议与主动提醒",
    "AirPlay sender UI": "AirPlay 投送界面",
    "Sports / News extras": "体育与新闻辅助",
    "Apple FairPlay DRM (no DRM-protected Apple content)": "受版权保护的 Apple 媒体",
    "Diagnostics extras (all telemetry to Apple)": "其他诊断服务",
    "Software update extras (keep softwareupdated + swcd)": "软件更新辅助服务",
    "Misc Apple legacy / unused": "其他 Apple 旧版服务",
    "Apple ID auth (kill — re-enable when needed for App Store)": "Apple 账号认证",
    "Apple Translate / Notes / VoiceOver / accessibility extras": "翻译、备忘录与辅助功能",
    "iCloud settings / language assets": "iCloud 设置与语言资源",
    "Apple TV watchlist / social / WiFi telemetry": "电视待看列表、社交与 Wi-Fi 统计",
    "T2 / bridgeOS": "T2 与 bridgeOS 更新",
    "Battery / power logging (charge limiting needs these)": "电池与电源记录",
    "Print (no printer)": "打印服务",
    "Xcode / iOS dev stack (FE/BE dev, no mobile)": "Xcode 与 iOS 开发",
    "Apple Music Player (AMP) suite — no Apple Music": "Apple Music 播放组件",
    "Apple TV+ / video subscriptions": "Apple TV+ 与视频订阅",
    "Touch Bar (M4 has none)": "触控栏",
    "Safari Web Inspector / WebKit push (no Safari)": "Safari 调试与 WebKit 推送",
    "iCloud Drive / Keychain Circle / SyncedDefaults / CloudDocs": "iCloud 云盘、钥匙串与设置同步",
    "MDM / Managed apps (no MDM enrollment)": "设备管理与受管应用",
    "Paired-device sync (Replicator)": "配对设备同步",
    "(uncategorized)": "未分类",
}


def section_title(title: str) -> str:
    translated = SECTION_TITLES.get(title, title)
    if translated and unicodedata.category(translated[0]) == "So":
        translated = translated.partition(" ")[2]
    return translated


def menu_section(sections: list[Section]) -> Section:
    flat = flat_index(sections)
    known = {it.label for it in flat}
    sizes = {name: len({it.label for sec in resolve_preset(sections, name)
                        for it in sec.items} & known) for name in BUILTIN_PRESETS}
    items = [
        Item(label="预览并应用当前选择", section=MENU_SECTION, action="apply",
             comment="查看逐项计划，确认后执行；勾选代表允许启动，不代表进程正在运行。"),
        Item(label="全量精简 · 保留 AirDrop 与应用搜索", section=MENU_SECTION,
             action="preset:" + EXTREME_PRESET,
             comment=f"准备 {sizes[EXTREME_PRESET]} 项。会影响 iCloud、Siri、商店、更新安装等功能；保留必要依赖。"),
        Item(label="遥测预设", section=MENU_SECTION, action="preset:telemetry",
             comment=f"准备 {sizes['telemetry']} 项：统计、诊断、崩溃报告、广告及测试版计划。"),
        Item(label="均衡预设 · 包含关闭 AirDrop", section=MENU_SECTION, action="preset:balanced",
             comment=f"准备 {sizes['balanced']} 项：遥测、Siri、AI、信息和互通。需要 AirDrop 请用上面的保留预设。"),
        Item(label="内存面板 · 应用占用与缓存回收", section=MENU_SECTION, action="memory",
             comment="按应用合并辅助进程，查看内存压力、压缩与交换；可单独请求应用释放缓存。"),
    ]
    if not all(it.disabled for it in flat):
        items.append(Item(label="关闭整个目录 · 包含保留功能", section=MENU_SECTION,
                          action="disable-all", comment="包含 AirDrop 和应用搜索；会影响账号登录、商店和更新安装。"))
    if any(it.disabled or it.disabled_domains for it in flat):
        items.append(Item(label="启用整个目录", section=MENU_SECTION, action="enable-all",
                          comment="准备启用目录中的全部服务，并移除开机保持；按快照恢复请使用下方入口。"))
    if (BACKUP_DIR / "latest.json").exists():
        items.append(Item(label="恢复最近一次快照", section=MENU_SECTION, action="restore",
                          comment="恢复各域原始配置、Spotlight 及当时的开机保持选择。"))
    items += [Item(label="查看最近执行结果", section=MENU_SECTION, action="report",
                   comment="查看已停止、仍运行、SIP 拒绝卸载及配置失败的具体服务。"),
              Item(label="SIP 与操作说明", section=MENU_SECTION, action="help",
                   comment="了解系统保护、状态含义和键盘操作。")]
    return Section(title=MENU_SECTION, items=items)


def select_for_action(sections: list[Section], action: str) -> str:
    flat = flat_index(sections)
    if action in ("disable-all", "enable-all"):
        for it in flat:
            it.selected = action == "enable-all"
        name = "关闭整个目录" if action == "disable-all" else "启用整个目录"
    else:
        preset = action.split(":", 1)[1]
        wanted = {it.label for sec in resolve_preset(sections, preset) for it in sec.items}
        for it in flat:
            if preset == EXTREME_PRESET:
                it.selected = bool(preserve_reason(it.label))
            else:
                it.selected = (False if it.label in wanted else None if it.disabled_domains and not it.disabled else not it.disabled)
        name = {EXTREME_PRESET: "全量保留预设", "telemetry": "遥测预设", "balanced": "均衡预设"}.get(preset, preset)
    kept = keep_locked_on(sections)
    return f"已准备{name}；按 a 预览并应用" + (f"；跳过 {kept} 项 SIP 受限服务" if kept else "")


def jump_section(sections: list[Section], flat: list[Item], cursor: int, direction: int) -> int:
    starts = []
    count = 0
    for sec in sections:
        if sec.items:
            starts.append(count)
            count += len(sec.items)
    if direction > 0:
        return next((i for i in starts if i > cursor), cursor)
    return next((i for i in reversed(starts) if i < cursor), cursor)


def display_width(text: str) -> int:
    return sum(0 if unicodedata.combining(c) else
               2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def clip_text(text: str, width: int) -> str:
    used = 0
    out = []
    for char in text:
        size = display_width(char)
        if used + size > width:
            break
        out.append(char)
        used += size
    return "".join(out)


def pad_text(text: str, width: int) -> str:
    text = clip_text(text, width)
    return text + " " * (width - display_width(text))


def wrap_text(text: str, width: int) -> list[str]:
    lines = []
    for paragraph in text.split("\n"):
        while display_width(paragraph) > width:
            line = clip_text(paragraph, width)
            lines.append(line)
            paragraph = paragraph[len(line):]
        lines.append(paragraph)
    return lines


def put_line(screen, y: int, text: str, attr=0) -> None:
    h, w = screen.getmaxyx()
    if 0 <= y < h and w > 1:
        screen.addstr(y, 0, clip_text(text, w - 1), attr)


def filtered_sections(sections: list[Section], query: str, mode: int) -> list[Section]:
    result = []
    for sec in sections:
        items = []
        changed = set(sum(pending_changes([sec]), []))
        for it in sec.items:
            text = f"{it.label} {it.comment} {section_title(sec.title)} {preserve_reason(it.label)}".casefold()
            if query.casefold() not in text:
                continue
            if mode == 1 and not (it.disabled and it.pids):
                continue
            if mode == 2 and not preserve_reason(it.label):
                continue
            if mode == 3 and it.label not in changed:
                continue
            items.append(it)
        if items:
            result.append(Section(title=sec.title, items=items))
    return result


SPINNER = "▖▘▝▗"
SPINNER_MS = 140
SPOTLIGHT_POLL_S = 3


def draw(stdscr, sections: list[Section], cursor: int, status: str, scroll: list[int],
         tick: int = 0, *, all_sections: list[Section] | None = None,
         overview: str = "", query: str = "", mode: int = 0) -> None:
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    put_line(stdscr, 0, f"macOS 精简 {VERSION}  ·  服务配置与实际运行状态", curses.A_BOLD)
    if h < 18 or w < 62:
        put_line(stdscr, 2, "请将终端扩大到至少 62 列、18 行。按 q 退出。")
        stdscr.refresh()
        return
    services = flat_index(all_sections if all_sections is not None else sections)
    services = [it for it in services if not it.action and it.section != SPOTLIGHT_SECTION]
    off = sum(it.disabled for it in services)
    alive = sum(bool(it.pids) for it in services)
    ignored = sum(it.disabled and bool(it.pids) for it in services)
    put_line(stdscr, 1, f"本机 {len(services)} 项  |  禁用 {off}  |  运行 {alive}  |  禁用仍运行 {ignored}")
    put_line(stdscr, 2, overview, curses.A_DIM)
    put_line(stdscr, 3, "─" * (w - 1), curses.A_DIM)
    changes = sum(len(part) for part in pending_changes(all_sections or sections))
    put_line(stdscr, 4, f"筛选：{FILTERS[mode]}  搜索：{query or '无'}  待更改：{changes}")
    put_line(stdscr, 5, "选择  " + pad_text("当前配置 / 运行", 18) + "服务或操作", curses.A_DIM)
    flat = flat_index(sections)
    current = flat[cursor] if flat else None
    rows = []
    selected_row = 0
    for sec in sections:
        rows.append(("─ " + section_title(sec.title), curses.A_DIM))
        for it in sec.items:
            if it is current:
                selected_row = len(rows)
            if it.action:
                text = "  ›   " + pad_text("准备预设" if it.action.startswith("preset:") else "操作", 18) + it.label
            else:
                changed = (it.selected is True and bool(it.disabled or it.disabled_domains)) or (it.selected is False and not it.disabled)
                mark = "[-]" if it.selected is None else "[✓]" if it.selected else "[ ]"
                state = STATUS_TEXT[service_status(it)]
                if it.section == SPOTLIGHT_SECTION:
                    state = "索引待确认 " + SPINNER[tick % 4] if it.state == "indexing" else "索引开启" if it.state == "on" else "索引关闭"
                    changed = it.selected == it.disabled
                text = ("*" if changed else " ") + mark + "  " + pad_text(state, 18) + it.label
            attr = curses.A_REVERSE if it is current else curses.A_DIM if it.is_locked else 0
            rows.append((text, attr))
    height = h - 12
    scroll[0] = max(0, min(scroll[0], max(0, len(rows) - height)))
    if selected_row < scroll[0]:
        scroll[0] = selected_row
    elif selected_row >= scroll[0] + height:
        scroll[0] = selected_row - height + 1
    if not flat:
        put_line(stdscr, 6, "没有匹配服务。按 / 修改搜索，按 f 切换筛选，按 Esc 清空。")
    for i, (text, attr) in enumerate(rows[scroll[0]:scroll[0] + height]):
        put_line(stdscr, 6 + i, text, attr)
    put_line(stdscr, h - 6, "─" * (w - 1), curses.A_DIM)
    if current:
        about = current.comment
        if not current.action and current.section != SPOTLIGHT_SECTION:
            detail = f"{current.label}  域：{', '.join(sorted(current.domains))}"
            if current.pids:
                detail += "  PID：" + ",".join(map(str, current.pids))
            about = ("保留用途：" + preserve_reason(current.label) + "。 " if preserve_reason(current.label) else "") + about
            if current.is_locked:
                about = "SIP 受限，默认跳过禁用；允许恢复启用。 " + about
            put_line(stdscr, h - 5, detail, curses.A_DIM)
        else:
            put_line(stdscr, h - 5, current.label, curses.A_BOLD)
        put_line(stdscr, h - 4, about, curses.A_DIM)
    put_line(stdscr, h - 3, "↑↓ 移动  空格 选择  [ ] 分组  / 搜索  f 筛选  r 刷新")
    put_line(stdscr, h - 2, "回车 预览  a 应用  m 内存  v 结果  ? 帮助  q 退出")
    put_line(stdscr, h - 1, status or "[✓] 允许启动  [ ] 请求禁用  * 待更改；选择不等于执行结果。", curses.A_BOLD)
    stdscr.refresh()


def show_dialog(stdscr, title: str, lines: list[str], *, confirm: bool = False) -> bool:
    offset = 0
    stdscr.timeout(-1)
    while True:
        h, w = stdscr.getmaxyx()
        body = [part for line in lines for part in wrap_text(line, max(1, w - 2))]
        height = max(1, h - 4)
        offset = max(0, min(offset, max(0, len(body) - height)))
        stdscr.erase()
        put_line(stdscr, 0, title, curses.A_BOLD)
        for i, line in enumerate(body[offset:offset + height]):
            put_line(stdscr, i + 2, line)
        footer = "↑↓ / PgUp PgDn 滚动  y 确认执行  其他键取消" if confirm else "↑↓ / PgUp PgDn 滚动  回车 / Esc 返回"
        put_line(stdscr, h - 1, footer, curses.A_REVERSE)
        stdscr.refresh()
        key = stdscr.getch()
        if key in (curses.KEY_DOWN, ord('j')):
            offset += 1
        elif key in (curses.KEY_UP, ord('k')):
            offset -= 1
        elif key == curses.KEY_NPAGE:
            offset += height
        elif key == curses.KEY_PPAGE:
            offset -= height
        elif key == curses.KEY_RESIZE:
            continue
        elif confirm:
            return key in (ord('y'), ord('Y'))
        elif key in (10, 13, 27, ord('q')):
            return False


def read_search(stdscr, previous: str, prompt: str = "搜索名称 / 分类 / 说明：") -> str:
    value = previous
    stdscr.timeout(-1)
    curses.curs_set(1)
    try:
        while True:
            h, w = stdscr.getmaxyx()
            stdscr.move(h - 1, 0)
            stdscr.clrtoeol()
            put_line(stdscr, h - 1, prompt + value)
            stdscr.refresh()
            key = stdscr.get_wch()
            if key in ('\n', '\r', curses.KEY_ENTER):
                return value
            if key == '\x1b':
                return previous
            if key in ('\x7f', '\b', curses.KEY_BACKSPACE):
                value = value[:-1]
            elif isinstance(key, str) and key.isprintable():
                value += key
    finally:
        curses.curs_set(0)


def preview_lines(sections: list[Section], spotlight: Section) -> list[str]:
    disable, enable = pending_changes(sections)
    stop = [it.label for it in flat_index(sections) if it.selected is False and not it.is_locked and it.pids]
    lines = [f"配置禁用 {len(disable)} 项；恢复启用 {len(enable)} 项；目标中仍运行 {len(stop)} 项。",
             "确认后先保存恢复快照，再执行。禁用标记不保证服务停止；SIP 拒绝会逐项报告。", ""]
    selected = {it.label: it for it in flat_index(sections)}
    for label in sorted(set(disable) | set(stop)):
        it = selected[label]
        lines.append(f"禁用/停止  {label}" + ("  [SIP 受限，可能被拒绝]" if it.is_sip_off_required else ""))
    lines.extend(f"恢复启用   {label}" for label in enable)
    spot = spotlight.items[0]
    if spot.selected == spot.disabled:
        lines += ["", "Spotlight：" + ("开启并重建索引" if spot.selected else "关闭文件索引与搜索")]
    preserved = [it.label for it in flat_index(sections) if it.selected and preserve_reason(it.label)]
    lines += ["", f"开机保持将保留启用 {len(preserved)} 个 AirDrop、搜索及基础依赖。"]
    return lines


def authenticate(stdscr) -> bool:
    curses.def_prog_mode()
    curses.endwin()
    try:
        return prime_sudo()
    finally:
        curses.reset_prog_mode()
        curses.curs_set(0)
        stdscr.clear()
        stdscr.refresh()


def run_tui(stdscr, sections: list[Section]) -> str | None:
    curses.curs_set(0)
    cursor, tick, mode = 0, 0, 0
    status, query = "", ""
    scroll = [0]
    spotlight = spotlight_section()
    polled_at = 0.0
    last_result = None

    def overview() -> str:
        p = persist_status()
        boot = "运行中" if p.get("running") else "已安装·未运行" if p["installed"] else "未安装"
        if p["installed"] and p.get("version") != VERSION:
            boot += "·待升级"
        return f"SIP：{'开启' if is_sip_enabled() else '关闭'}  |  开机保持：{boot}  |  配置与进程分别核对"

    heading = overview()

    def in_tui(text: str) -> None:
        h, _ = stdscr.getmaxyx()
        stdscr.move(h - 1, 0)
        stdscr.clrtoeol()
        put_line(stdscr, h - 1, text.strip(), curses.A_BOLD)
        stdscr.refresh()

    while True:
        matches = filtered_sections(sections, query, mode)
        view = matches if query or mode else [menu_section(sections), spotlight, *matches]
        flat = flat_index(view)
        cursor = max(0, min(cursor, len(flat) - 1))
        stdscr.timeout(SPINNER_MS if spotlight.items[0].state == "indexing" else -1)
        draw(stdscr, view, cursor, status, scroll, tick, all_sections=sections,
             overview=heading, query=query, mode=mode)
        key = stdscr.getch()
        if key == -1:
            tick += 1
            if time.time() - polled_at > SPOTLIGHT_POLL_S:
                refresh_spotlight(spotlight)
                polled_at = time.time()
            continue
        status = ""
        if key == ord('q'):
            return None
        if key == 27:
            query, mode, cursor = "", 0, 0
        elif key in (curses.KEY_DOWN, ord('j')):
            cursor = min(cursor + 1, len(flat) - 1)
        elif key in (curses.KEY_UP, ord('k')):
            cursor = max(0, cursor - 1)
        elif key in (curses.KEY_NPAGE, curses.KEY_PPAGE):
            cursor += 10 if key == curses.KEY_NPAGE else -10
        elif key in (ord('['), ord(']')):
            cursor = jump_section(view, flat, cursor, 1 if key == ord(']') else -1)
        elif key == ord('/'):
            query = read_search(stdscr, query)
            cursor, scroll[0] = 0, 0
        elif key == ord('f'):
            mode = (mode + 1) % len(FILTERS)
            cursor, scroll[0] = 0, 0
        elif key == ord('r'):
            refresh_state(sections)
            refresh_spotlight(spotlight)
            heading = overview()
            status = "已重新读取系统状态，未执行的选择已重置。"
        elif key == ord('m'):
            run_memory_tui(stdscr)
        elif key == ord(' ') and flat:
            it = flat[cursor]
            if it.is_locked and it.selected:
                status = "该项预计受 SIP 限制；默认跳过。按 ? 查看说明。"
            elif not it.action:
                it.selected = not it.selected
        elif key in (10, 13, curses.KEY_ENTER, ord('a'), ord('v'), ord('?')):
            action = 'apply' if key == ord('a') else 'report' if key == ord('v') else 'help' if key == ord('?') else flat[cursor].action if flat else ''
            if action == 'memory':
                run_memory_tui(stdscr)
                continue
            if action.startswith('preset:') or action in ('disable-all', 'enable-all'):
                status = select_for_action(sections, action)
                if action == 'preset:' + EXTREME_PRESET:
                    spotlight.items[0].selected = True
                continue
            if action == 'help':
                show_dialog(stdscr, '操作说明与 SIP', [
                    '空格修改选择；预设只准备计划。按 a 预览，再按 y 执行。',
                    '按 / 搜索服务、中文分类或说明；f 切换全部、禁用仍运行、保留功能、待更改。',
                    '按 r 重新读取系统，未执行的选择会重置。Esc 清空搜索与筛选。', '',
                    '按 m 打开内存面板；在面板中按 o 预览应用缓存回收，按 v 查看回收结果。', '',
                    '已禁用·未运行：禁用配置已写入，当前没有运行进程。',
                    '禁用但仍运行：配置已写入，但进程仍在运行。',
                    '部分域仍禁用：同一服务在不同域中的配置不一致；[-] 保留当前各域状态。',
                    '允许·按需启动：服务可启动，当前没有运行进程。', '',
                    'SIP 是系统完整性保护；管理员权限也不能卸载某些受保护服务。',
                    '默认跳过标记为 SIP 受限的服务。--attempt-protected 只允许尝试，不会关闭 SIP。',
                    '系统可能通过 Mach/XPC 请求重新启动未成功卸载的服务。重复杀进程不能解决卸载限制。',
                    '调整 SIP 需要恢复环境与重启，会降低系统保护。', '',
                    f'快照：{BACKUP_DIR / "snapshots"}',
                    f'执行结果：{BACKUP_DIR / "last-apply.json"}',
                ])
                continue
            if action == 'report':
                path = BACKUP_DIR / 'last-apply.json'
                result = last_result or (json.loads(path.read_text()) if path.exists() else None)
                lines = [apply_summary(result), '', apply_problems(result), '',
                         '快照：' + result.get('snapshot', '见快照目录')] if result else ['尚无执行记录。']
                show_dialog(stdscr, '最近执行结果', lines)
                continue
            if action == 'restore':
                snap = load_snapshot()
                if not snap:
                    status = '没有可恢复的快照。'
                    continue
                if show_dialog(stdscr, '恢复最近一次快照', [
                        f"时间：{snap.get('created_at', '未记录')}",
                        '恢复各域原始启动配置、Spotlight 和当时的开机保持选择。',
                        '当前的选择不会应用；快照原件保留。'], confirm=True):
                    if authenticate(stdscr):
                        output = io.StringIO()
                        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                            code = cmd_restore(sections)
                        refresh_state(sections)
                        refresh_spotlight(spotlight)
                        heading = overview()
                        show_dialog(stdscr, '恢复完成' if code == 0 else '恢复尚未全部完成', output.getvalue().splitlines())
                continue
            selected = {it.label: it.selected for it in flat_index(sections)}
            refresh_state(sections)
            for it in flat_index(sections):
                it.selected = selected[it.label]
            spot = spotlight.items[0]
            stop = any(it.pids and it.selected is False and not it.is_locked for it in flat_index(sections))
            if (not any(pending_changes(sections)) and not stop and spot.selected != spot.disabled
                    and not persistence_needs_update(sections)):
                status = '当前没有待执行更改。'
                continue
            if not show_dialog(stdscr, '执行预览', preview_lines(sections, spotlight), confirm=True):
                status = '未执行，选择已保留。'
                continue
            if not authenticate(stdscr):
                status = '管理员认证未完成，未执行。'
                continue
            last_result = execute_selection(sections, report=in_tui)
            if spot.selected == spot.disabled:
                ok, message = spotlight_set(spot.selected, report=in_tui)
                last_result['spotlight_message'] = message
                if not ok:
                    last_result['spotlight_error'] = message
                refresh_spotlight(spotlight)
                save_apply_report(last_result)
            heading = overview()
            show_dialog(stdscr, '执行结果 · 部分未完成' if apply_incomplete(last_result) else '执行结果',
                        [apply_summary(last_result), last_result.get('spotlight_message', ''),
                         last_result.get('persist_message', ''), '', apply_problems(last_result), '',
                         '恢复快照：' + last_result['snapshot']])
            status = '结果已保存，按 v 查看；按 r 更新运行状态。'

def macos_major() -> int | None:
    r = subprocess.run(["sw_vers", "-productVersion"],
                       capture_output=True, text=True, check=False)
    part = r.stdout.strip().split(".")[0]
    return int(part) if part.isdigit() else None


def mem_free_mb() -> int | None:
    """Reclaimable RAM (free + inactive + speculative + purgeable) in MB."""
    r = subprocess.run(["vm_stat"], capture_output=True, text=True, check=False)
    if r.returncode != 0:
        return None
    page = re.search(r"page size of (\d+) bytes", r.stdout)
    if not page:
        return None
    page_bytes = int(page.group(1))
    pages = 0
    for key in ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable"):
        m = re.search(rf"{key}:\s+(\d+)\.", r.stdout)
        if m:
            pages += int(m.group(1))
    return pages * page_bytes // (1024 * 1024)


def write_snapshot(sections: list[Section]) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    states = {d: domain_state(d) for d in DOMAINS}
    domains = {}
    for domain, (registered, disabled) in states.items():
        present = sorted(it.label for sec in sections for it in sec.items if domain in it.domains)
        domains[domain] = {
            "present": present,
            "disabled": sorted(set(present) & disabled),
            "running": sorted(label for label in present if registered.get(label, 0)),
        }
    stamp = datetime.now().astimezone().isoformat()
    build = subprocess.run(["sw_vers", "-buildVersion"], capture_output=True, text=True, check=False).stdout.strip()
    snap = {"created_at": stamp, "build": build, "uid": UID, "domains": domains,
            "disabled": [it.label for sec in sections for it in sec.items if it.disabled],
            "spotlight": spotlight_state(),
            "persist": json.loads(PERSIST_STATE.read_text()) if PERSIST_STATE.exists() else None}
    payload = json.dumps(snap, ensure_ascii=False, indent=2)
    archive = BACKUP_DIR / "snapshots" / (datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".json")
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text(payload)
    (BACKUP_DIR / "latest.json").write_text(payload)
    if os.geteuid() == 0 and UID:
        for target in (BACKUP_DIR, archive.parent, archive, BACKUP_DIR / "latest.json"):
            os.chown(target, UID, pwd.getpwuid(UID).pw_gid)
    return archive


def load_snapshot() -> dict | None:
    path = BACKUP_DIR / "latest.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def run_apply(sections: list[Section], dry_run: bool) -> int:
    to_disable, to_enable = pending_changes(sections)
    wanted_off = [it.label for sec in sections for it in sec.items if it.selected is False and not it.is_locked]
    running = running_pids(wanted_off)
    update_persist = persistence_needs_update(sections)
    if not to_disable and not to_enable and not running and not update_persist:
        print("无需更改：已处于请求状态")
        return 0
    if dry_run:
        print(f"[dry-run] 配置禁用 {len(to_disable)} 项，恢复启用 {len(to_enable)} 项；目标中仍运行 {len(running)} 项")
        if update_persist:
            print("  开机保持将同步当前选择与程序版本")
        for label in sorted(set(to_disable) | set(running)):
            print(f"  disable  {label}")
        for label in to_enable:
            print(f"  enable   {label}")
        return 0
    if not prime_sudo():
        print("需要管理员权限", file=sys.stderr)
        return 1
    result = execute_selection(sections)
    print(f"恢复快照：{result['snapshot']}", flush=True)
    print(apply_summary(result))
    if result.get("persist_message"):
        print(result["persist_message"])
    sys.stderr.write(apply_problems(result))
    return 2 if apply_incomplete(result) else 0


def execute_selection(sections: list[Section], report=progress) -> dict:
    snapshot = write_snapshot(sections)
    result = apply_changes(sections, report=report, retry_running=True)
    result["snapshot"] = str(snapshot)
    result["recorded_at"] = datetime.now().astimezone().isoformat()
    try:
        result["persist_message"] = sync_boot_daemon(sections)
    except (OSError, subprocess.CalledProcessError) as error:
        result["persist_error"] = (getattr(error, "stderr", "") or str(error)).strip()
    refresh_state(sections)
    result["services"] = [service_record(it) for sec in sections for it in sec.items]
    save_apply_report(result)
    return result


def save_apply_report(result: dict) -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    report_path = BACKUP_DIR / "last-apply.json"
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    if os.geteuid() == 0 and UID:
        os.chown(report_path, UID, pwd.getpwuid(UID).pw_gid)


def cmd_status(sections: list[Section], as_json: bool) -> int:
    refresh_state(sections)
    items = flat_index(sections)
    services = [service_record(it) for it in items]
    disabled = sum(it.disabled for it in items)
    running = {it.label: it.pids for it in items if it.pids}
    ignored = sorted(it.label for it in items if it.disabled and it.pids)
    partial = sorted(it.label for it in items if service_status(it) == "partial-disabled")
    spot, stats = spotlight_state(), catalog_stats()
    persist, sip = persist_status(), is_sip_enabled()
    payload = {
        "catalog": {"total": stats.total, "sections": stats.sections,
                    "telemetry": stats.telemetry, "balanced": stats.balanced},
        "labels_present": len(items), "disabled": disabled, "enabled": len(items) - disabled,
        "running": sorted(running), "disabled_but_running": ignored,
        "disabled_stopped": sorted(it.label for it in items if it.disabled and not it.pids),
        "partial_disabled": partial, "services": services,
        "spotlight": spot, "persist": persist,
        "sip": {"enabled": sip, "locked": sum(it.is_locked for it in items)},
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(f"macOS 精简 {VERSION} — 当前状态")
    print(f"本机 {len(items)} 项 / 目录 {stats.total} 项；SIP：{'开启' if sip else '关闭'}")
    print(f"已禁用且未运行 {len(payload['disabled_stopped'])} 项；禁用但仍运行 {len(ignored)} 项；"
          f"部分域禁用 {len(partial)} 项；允许启动 {len(items) - disabled - len(partial)} 项")
    print(f"Spotlight：{ {'on': '索引开启', 'off': '索引关闭', 'indexing': '索引状态待确认或重建中'}[spot] }")
    if persist["installed"]:
        print(f"开机保持：{'运行中' if persist.get('running') else '已安装但未运行'}；"
              f"保存禁用 {persist['labels']} 项、保留启用 {len(persist['enabled_labels'])} 项")
    else:
        print("开机保持：未安装")
    problems = [it for it in items if it.label in ignored or it.label in partial]
    if problems:
        print("\n需要关注的服务（禁用配置不等于停止）：")
        for it in problems:
            print(f"  {STATUS_TEXT[service_status(it)]}  {it.label}  PID={it.pids or '无'}")
    print(f"\n执行原因与历史结果：--report；完整逐项状态：--status --json")
    return 0


def cmd_report(as_json: bool) -> int:
    path = BACKUP_DIR / "last-apply.json"
    if not path.exists():
        print("尚无执行记录", file=sys.stderr)
        return 1
    result = json.loads(path.read_text())
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("最近执行结果：" + result.get("recorded_at", "时间未记录"))
        print(apply_summary(result))
        print(apply_problems(result), end="")
        if result.get("snapshot"):
            print("恢复快照：" + result["snapshot"])
    return 0


def cmd_audit(sections: list[Section], absent: list[str],
              version_skip: list[str] | None = None) -> int:
    stats = catalog_stats()
    present = sum(len(sec.items) for sec in sections)
    major = macos_major()
    print(f"目录：{stats.total} 个服务 / {stats.sections} 个分类")
    print(f"本机存在 {present} 项" + (f"；macOS {major}" if major is not None else ""))
    if version_skip:
        print(f"\n不适用于当前系统版本的 {len(version_skip)} 项：")
        for label in version_skip:
            print(f"  {label}")
    if absent:
        print(f"\n本机不存在、已跳过的 {len(absent)} 项：")
        for label in absent:
            print(f"  {label}")
    if not absent and not version_skip:
        print("目录中的全部服务均适用于本机。")
    return 0


def persist_status() -> dict:
    if not PERSIST_PLIST.exists():
        return {"installed": False}
    state = json.loads(PERSIST_STATE.read_text())
    last = json.loads(PERSIST_REPORT.read_text()) if PERSIST_REPORT.exists() else {}
    process = subprocess.run(["launchctl", "print", f"system/{PERSIST_LABEL}"],
                             capture_output=True, text=True, check=False)
    return {"installed": True, "running": process.returncode == 0 and "state = running" in process.stdout,
            "version": state.get("version"),
            "labels": len(state["labels"]), "enabled_labels": state.get("enabled", []),
            "last_boot": last.get("boot"), "respawners": last.get("respawners")}


def sudo_write(path: Path, text: str) -> None:
    subprocess.run(["sudo", "tee", str(path)], input=text, text=True,
                   stdout=subprocess.DEVNULL, check=True)


def script_for_daemon() -> tuple[Path | None, str]:
    source = Path(__file__).resolve()
    return (source, "") if source.is_file() else (None, "请先下载仓库再执行")


def persistence_selection(sections: list[Section]) -> dict:
    return {
        "uid": UID, "version": VERSION,
        "labels": sorted(it.label for sec in sections for it in sec.items if it.selected is False),
        "enabled": sorted(it.label for sec in sections for it in sec.items
                          if it.selected and preserve_reason(it.label)),
    }


def persistence_needs_update(sections: list[Section]) -> bool:
    wanted = persistence_selection(sections)
    installed = PERSIST_PLIST.exists()
    if not wanted["labels"] or not is_sip_enabled():
        return installed
    return not installed or not PERSIST_STATE.exists() or json.loads(PERSIST_STATE.read_text()) != wanted


def sync_boot_daemon(sections: list[Section], saved_state: dict | None = None) -> str:
    """Install and start the saved selection, or remove it when no longer needed."""
    state = {**saved_state, "version": VERSION} if saved_state is not None else persistence_selection(sections)
    labels = state["labels"]
    is_installed = PERSIST_PLIST.exists()
    if labels and is_sip_enabled():
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{PERSIST_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>{PERSIST_DIR}/debloat.py</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>{PERSIST_DIR}/daemon.log</string>
  <key>StandardErrorPath</key><string>{PERSIST_DIR}/daemon.log</string>
</dict>
</plist>
"""
        source, why_not = script_for_daemon()
        if source is None:
            raise OSError(f"无法安装开机保持：{why_not}")
        subprocess.run(["sudo", "mkdir", "-p", str(PERSIST_DIR)], capture_output=True, check=True)
        # Root runs this copy at every boot, so it must not stay writable by the user.
        subprocess.run(["sudo", "install", "-m", "644", "-o", "root", "-g", "wheel",
                        str(source.resolve()), str(PERSIST_DIR / "debloat.py")],
                       capture_output=True, check=True)
        sudo_write(PERSIST_STATE, json.dumps(state, indent=2))
        if is_installed:
            subprocess.run(["sudo", "launchctl", "bootout", f"system/{PERSIST_LABEL}"],
                           capture_output=True, check=False)
        sudo_write(PERSIST_PLIST, plist)
        subprocess.run(["sudo", "chown", "root:wheel", str(PERSIST_PLIST)], capture_output=True, check=True)
        subprocess.run(["sudo", "launchctl", "bootstrap", "system", str(PERSIST_PLIST)],
                       capture_output=True, text=True, check=True)
        return (f"开机保持已启动：禁用 {len(labels)} 项，保留启用 {len(state.get('enabled', []))} 项。"
                "受 SIP 保护的服务仍可能运行。")
    if not is_installed and not PERSIST_DIR.exists():
        return ""
    subprocess.run(["sudo", "launchctl", "bootout", f"system/{PERSIST_LABEL}"],
                   capture_output=True, check=False)
    subprocess.run(["sudo", "rm", "-f", str(PERSIST_PLIST)], check=True)
    subprocess.run(["sudo", "rm", "-rf", str(PERSIST_DIR)], check=True)
    return "开机保持已移除：" + ("没有禁用目标" if not labels else "SIP 已关闭，使用系统持久配置")


def kill_label(label: str, pids: list[int]) -> None:
    for domain in DOMAINS:
        subprocess.run(["launchctl", "bootout", f"{domain}/{label}"],
                       capture_output=True, check=False)
    for pid in pids:
        subprocess.run(["kill", "-9", str(pid)], capture_output=True, check=False)


def reapply_persisted(state: dict) -> dict:
    wanted = set(state["labels"])
    labels = wanted | set(state.get("enabled", []))
    sections = parse_labels("\n".join(sorted(labels)))
    drop_absent_labels(sections)
    refresh_state(sections)
    for sec in sections:
        for it in sec.items:
            it.selected = it.label not in wanted
    return apply_changes(sections, report=lambda _text: None, stop_processes=False)


def cmd_persist_run() -> int:
    """The boot daemon, as root: re-disable the saved set before login, again
    once the user's gui domain exists, then check a minute apart for the first
    passes and every few minutes after that for as long as the Mac is up. A
    running label is killed once; one running again after its kill respawns on
    its own and is left alone rather than killed on every pass."""
    global UID, DOMAINS
    state = json.loads(PERSIST_STATE.read_text())
    UID = state["uid"]
    DOMAINS = ("system", f"gui/{UID}")
    wanted = set(state["labels"])
    run: dict = {"boot": time.strftime("%Y-%m-%dT%H:%M:%S"), "wanted": len(wanted), "passes": []}

    def reapply(step: str) -> None:
        result = reapply_persisted(state)
        run[step] = {key: result[key] for key in
                     ("disabled", "enabled", "killed", "not_disabled", "not_enabled", "sip_blocked")}

    reapply("at_boot")
    while os.stat("/dev/console").st_uid != UID:
        time.sleep(2)
    run["login"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    reapply("at_login")

    killed: set[str] = set()
    respawners: set[str] = set()
    n = 0
    while True:
        n += 1
        time.sleep(PERSIST_PASS_S if n <= PERSIST_PASSES else PERSIST_WATCH_S)
        reapply("last_pass_overrides")
        running = running_pids(sorted(wanted))
        fresh = {label: pids for label, pids in running.items() if label not in killed}
        respawners |= set(running) - set(fresh)
        for label, pids in fresh.items():
            kill_label(label, pids)
        killed |= set(fresh)
        entry = {"pass": n, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "killed": sorted(fresh)}
        run["passes"] = (run["passes"] + [entry])[-PERSIST_REPORT_PASSES:]
        run["killed_total"] = len(killed)
        run["respawners"] = sorted(respawners)
        PERSIST_REPORT.write_text(json.dumps(run, indent=2))


def cmd_restore(sections: list[Section], snapshot_path: Path | None = None) -> int:
    snap = json.loads(snapshot_path.read_text()) if snapshot_path else load_snapshot()
    if snap is None:
        print(f"没有恢复快照：{BACKUP_DIR / 'latest.json'}", file=sys.stderr)
        return 1
    if snap["uid"] != UID:
        print("该快照属于另一个用户", file=sys.stderr)
        return 1
    if not prime_sudo():
        return 1
    failures = []
    for domain, state in snap["domains"].items():
        _, disabled_now = domain_state(domain)
        previous = set(state["disabled"])
        for label in state["present"]:
            should_disable = label in previous
            if should_disable == (label in disabled_now):
                continue
            action = "disable" if should_disable else "enable"
            result = subprocess.run(["sudo", "launchctl", action, f"{domain}/{label}"],
                                    capture_output=True, text=True, check=False)
            if result.returncode:
                failures.append({"domain": domain, "label": label, "error": result.stderr.strip()})
            if not should_disable and not result.returncode:
                error = bootstrap_if_absent(label, domain)
                if error:
                    failures.append({"domain": domain, "label": label, "error": error})
            if should_disable and label not in state["running"]:
                subprocess.run(["sudo", "launchctl", "bootout", f"{domain}/{label}"],
                               capture_output=True, check=False)
    previous_persist = snap.get("persist")
    try:
        if previous_persist:
            sync_boot_daemon(sections, saved_state=previous_persist)
        else:
            sync_boot_daemon([])
    except (OSError, subprocess.CalledProcessError) as error:
        failures.append({"persist": str(error)})
    was_on = snap.get("spotlight") != "off"
    if was_on != (spotlight_state() != "off"):
        ok, message = spotlight_set(was_on)
        print(message)
        if not ok:
            failures.append({"spotlight": message})
    # Enabling restores launch permission; launchd starts on-demand jobs when needed.
    for domain, state in snap["domains"].items():
        _, disabled_now = domain_state(domain)
        mismatched = (disabled_now & set(state["present"])) ^ set(state["disabled"])
        failures.extend({"domain": domain, "label": label, "error": "恢复后的状态与快照不一致"}
                        for label in sorted(mismatched))
    print(f"恢复完成；未恢复项目 {len(failures)} 个。原始快照已保留。")
    if failures:
        print(json.dumps(failures, ensure_ascii=False, indent=2), file=sys.stderr)
    return 2 if failures else 0


def load_sections() -> tuple[list[Section], str, list[str], list[str]]:
    sections = parse_labels(EMBEDDED_LABELS)
    stats = catalog_stats(sections)
    note = (f"{stats.total} embedded labels in {stats.sections} sections "
            f"(telemetry {stats.telemetry}, balanced {stats.balanced})")
    if USER_LABELS_FILE.exists():
        known = {it.label for sec in sections for it in sec.items}
        added = 0
        for sec in parse_labels(USER_LABELS_FILE):
            fresh: list[Item] = []
            for it in sec.items:
                if it.label not in known:
                    known.add(it.label)
                    fresh.append(it)
            sec.items = fresh
            sec.preset = ""
            if sec.items:
                sections.append(sec)
                added += len(sec.items)
        note += f" + {added} from {USER_LABELS_FILE}"
    global CATALOG_TOTAL
    CATALOG_TOTAL = sum(len(sec.items) for sec in sections)
    version_skip = drop_wrong_macos(sections)
    absent = drop_absent_labels(sections)
    return sections, note, absent, version_skip


def format_labels(sections: list[Section]) -> str:
    lines: list[str] = []
    for sec in sections:
        lines.append(f"# === {sec.title} ===")
        for it in sec.items:
            name = f"{it.label} {SIP_OFF_TAG}" if it.is_sip_off_required else it.label
            lines.append(f"{name.ljust(45)} # {it.comment}" if it.comment else name)
        lines.append("")
    return "\n".join(lines)


def resolve_preset(sections: list[Section], name: str) -> list[Section] | None:
    if name == EXTREME_PRESET:
        return [Section(title=sec.title, items=[it for it in sec.items if not preserve_reason(it.label)])
                for sec in sections]
    path = PRESETS_DIR / f"{name}.txt"
    if path.exists():
        return parse_labels(path)
    tiers = BUILTIN_PRESETS.get(name)
    if tiers is None:
        return None
    return [sec for sec in sections if sec.preset in tiers]


def cmd_preset(sections: list[Section], name: str, dry_run: bool) -> int:
    preset = resolve_preset(sections, name)
    if preset is None:
        print(f"未知预设 '{name}'。内置："
              f"{', '.join(BUILTIN_PRESETS)}；自定义目录：{PRESETS_DIR}",
              file=sys.stderr)
        return 1
    wanted = {it.label for sec in preset for it in sec.items}
    if not wanted:
        print(f"预设 '{name}' 没有适用于本机的服务", file=sys.stderr)
        return 1
    refresh_state(sections)
    known = {it.label for sec in sections for it in sec.items}
    unknown = sorted(wanted - known)
    for sec in sections:
        for it in sec.items:
            if name == EXTREME_PRESET:
                it.selected = bool(preserve_reason(it.label))
            elif it.label in wanted:
                it.selected = False
    print(f"预设 {name}：{len(wanted & known)} 项")
    if name == EXTREME_PRESET:
        for sec in sections:
            for it in sec.items:
                if preserve_reason(it.label):
                    print(f"  保留 {it.label} — {preserve_reason(it.label)}")
    sys.stdout.flush()
    if unknown:
        print(f"跳过目录外 {len(unknown)} 项；可通过 {USER_LABELS_FILE} 添加：", file=sys.stderr)
        for label in unknown:
            print(f"  {label}", file=sys.stderr)
    kept = keep_locked_on(sections)
    if kept:
        print(f"跳过 {kept} 项 SIP 受限服务；--attempt-protected 只允许尝试，不绕过保护。")
    return run_apply(sections, dry_run)


def run_memory_tui(stdscr) -> None:
    import memory_tools as memory

    def sample():
        put_line(stdscr, 0, '正在读取内存与应用进程…', curses.A_BOLD)
        stdscr.refresh()
        return memory.capture()

    cursor, query, status = 0, '', ''
    try:
        snapshot = sample()
        while True:
            rows = [r for r in snapshot['groups'] if query.casefold() in r['name'].casefold()]
            cursor = max(0, min(cursor, len(rows) - 1))
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            stdscr.timeout(-1)
            put_line(stdscr, 0, f'macOS 精简 {VERSION} · 内存与应用', curses.A_BOLD)
            if h < 18 or w < 62:
                put_line(stdscr, 2, '请将终端扩大到至少 62 列、18 行。按 q 返回。')
            else:
                m = snapshot['memory']
                put_line(stdscr, 1, f"压力：{m['pressure']}  物理内存：{memory.mib(m['physical_memory_bytes'])}  进程：{snapshot['process_count']}")
                put_line(stdscr, 2, f"压缩：{memory.mib(m['compressor_bytes'])}  Wired：{memory.mib(m['wired_bytes'])}  Swap：{memory.mib(m['swap_used_bytes'])}")
                put_line(stdscr, 3, f"文件缓存：{memory.mib(m['file_backed_bytes'])}  空闲与推测页：{memory.mib(m['free_and_speculative_bytes'])}")
                put_line(stdscr, 4, f"已读取 {snapshot['measured_count']} / {snapshot['process_count']} 个足迹  搜索：{query or '无'}", curses.A_DIM)
                put_line(stdscr, 6, pad_text('应用 / 进程组', w - 33) + pad_text('内存足迹', 15) + pad_text('RSS', 12) + '进程', curses.A_DIM)
                height = h - 11
                start = max(0, cursor - height + 1)
                for i, row in enumerate(rows[start:start + height]):
                    name = pad_text(clip_text(row['name'], w - 35), w - 33)
                    text = name + pad_text(memory.footprint_text(row), 15) + pad_text(memory.mib(row['rss_bytes']), 12) + str(row['process_count'])
                    put_line(stdscr, 7 + i, text, curses.A_REVERSE if start + i == cursor else 0)
                if not rows:
                    put_line(stdscr, 7, '没有匹配项。按 / 修改搜索。')
                put_line(stdscr, h - 3, '↑↓ 移动  / 搜索  r 刷新  o 缓存回收  v 结果  q 返回')
                put_line(stdscr, h - 2, '足迹与 RSS 不混加；≥ 为部分进程读数。回车查看所选项。', curses.A_DIM)
                put_line(stdscr, h - 1, status or '应用及其 Helper 合并展示；无法归属的系统进程单列。')
            stdscr.refresh()
            key = stdscr.getch()
            if key in (27, ord('q')):
                return
            if key in (curses.KEY_DOWN, ord('j')):
                cursor += 1
            elif key in (curses.KEY_UP, ord('k')):
                cursor -= 1
            elif key in (curses.KEY_NPAGE, curses.KEY_PPAGE):
                cursor += 10 if key == curses.KEY_NPAGE else -10
            elif key == ord('/'):
                query, cursor = read_search(stdscr, query, '搜索应用 / 进程：'), 0
            elif key == ord('r'):
                snapshot, status = sample(), '已刷新。'
            elif key == ord('v'):
                path = BACKUP_DIR / 'last-memory.json'
                lines = memory.report_lines(json.loads(path.read_text())) if path.exists() else ['尚无缓存回收记录。']
                show_dialog(stdscr, '最近缓存回收结果', lines)
            elif key in (10, 13, curses.KEY_ENTER) and rows:
                row = rows[cursor]
                show_dialog(stdscr, row['name'], [
                    f"内存足迹：{memory.footprint_text(row)}；RSS：{memory.mib(row['rss_bytes'])}",
                    f"进程数：{row['process_count']}；足迹可读：{row['measured_count']}",
                    'PID：' + ', '.join(map(str, row['pids'])),
                    '应用/进程组：' + row['id'], '',
                    '需要退出闲置应用时，请先保存工作，再在该应用中按 ⌘Q。',
                ])
            elif key == ord('o'):
                snapshot = sample()
                plan = memory.reclaim_plan(snapshot)
                if not plan['allowed']:
                    status = plan['reason']
                    continue
                if not show_dialog(stdscr, '应用缓存回收预览', [
                    plan['reason'],
                    '向应用发送 1 秒 warn 级别模拟内存压力，请应用释放可丢弃缓存。',
                    '完成后记录内存、交换活动和应用足迹的实际变化。',
                    '正在使用的应用数据仍会保留；可回收多少取决于应用响应。',
                ], confirm=True):
                    status = '已取消。'
                    continue
                if not authenticate(stdscr):
                    status = '管理员认证未完成。'
                    continue
                put_line(stdscr, 0, '正在请求回收并记录结果…', curses.A_BOLD)
                stdscr.refresh()
                result = memory.reclaim(BACKUP_DIR)
                show_dialog(stdscr, '缓存回收结果', memory.report_lines(result))
                snapshot = result.get('after', result['before'])
                status = '结果已保存，按 v 查看。'
    except (OSError, RuntimeError, ValueError) as exc:
        show_dialog(stdscr, '内存操作未完成', [str(exc)])


def cmd_memory(argv: list[str]) -> int:
    import memory_tools as memory

    actions = {'--memory', '--memory-ui', '--reclaim-memory', '--memory-report'}
    if len(actions.intersection(argv)) != 1 or any(arg not in actions | {'--json', '--dry-run'} for arg in argv):
        print('内存命令请单独使用：--memory、--memory-ui、--reclaim-memory 或 --memory-report。', file=sys.stderr)
        return 1
    if '--dry-run' in argv and '--reclaim-memory' not in argv:
        print('--dry-run 在内存命令中仅用于 --reclaim-memory。', file=sys.stderr)
        return 1
    as_json = '--json' in argv
    try:
        if '--memory-ui' in argv:
            if as_json or not reopen_tty_stdin():
                print('内存面板需要终端；JSON 查询请使用 --memory --json。', file=sys.stderr)
                return 1
            curses.wrapper(run_memory_tui)
            return 0
        if '--memory-report' in argv:
            path = BACKUP_DIR / 'last-memory.json'
            if not path.exists():
                print('尚无缓存回收记录。', file=sys.stderr)
                return 1
            data = json.loads(path.read_text())
            lines = memory.report_lines(data)
        else:
            data = memory.capture()
            lines = memory.snapshot_lines(data)
            if '--reclaim-memory' in argv:
                plan = memory.reclaim_plan(data)
                if '--dry-run' in argv:
                    data = {'status': 'preview', 'before': data, 'plan': plan}
                    lines += ['', plan['reason'], '计划：' + ' '.join(plan.get('command', []))]
                else:
                    if plan['allowed'] and not prime_sudo():
                        print('管理员认证未完成，未发送回收通知。', file=sys.stderr)
                        return 2
                    data = memory.reclaim(BACKUP_DIR)
                    lines = memory.report_lines(data)
        print(json.dumps(data, ensure_ascii=False, indent=2) if as_json else '\n'.join(lines))
        return 2 if data.get('status') in ('failed', 'skipped') else 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f'内存操作未完成：{exc}', file=sys.stderr)
        return 2


VERSION = "0.3.0"


def help_text() -> str:
    stats = catalog_stats()
    return f"""macos-debloat-zh {VERSION} — macOS 系统服务精简

目录：{stats.total} 个服务标识 / {stats.sections} 个分类；自动跳过本机不存在的服务。

用法：./macos_disable_bloat_services.sh [选项]
  无参数                              终端交互界面
  --preset extreme-keep-airdrop-search 全量精简，保留 AirDrop / 应用搜索 / 基础服务
  --preset telemetry                  遥测预设
  --preset balanced                   均衡预设（包括禁用 AirDrop）
  --dry-run                           只预览；单独使用时预览全量保留预设
  --attempt-protected                 对 SIP 标记项目也尝试一次，结果可能被系统拒绝
  --status / --verify [--json]         逐项配置、运行状态、PID 与所属域
  --report [--json]                   最近执行结果和逐项失败原因
  --memory [--json]                   内存压力、应用及辅助进程内存足迹
  --memory-ui                        直接打开中文内存面板
  --reclaim-memory [--dry-run]        请求应用释放缓存，保存前后实测结果
  --memory-report [--json]            最近缓存回收结果
  --list / --audit                     服务清单 / 不存在或不适用的服务
  --restore [快照.json]                恢复最近一次或指定快照的各域状态
  --enable-all                        启用整个目录，移除开机保持程序
  --disable-all                       禁用整个目录，包括 AirDrop 和应用搜索
  --disable-sip / --enable-sip         请求系统修改 SIP，物理 Mac 通常需要进入恢复环境

快照位置：{BACKUP_DIR}/snapshots
SIP 开启时，成功写入禁用标记不保证服务停止；退出码 2 表示部分目标未达成。
"""


def main() -> int:
    argv = sys.argv[1:]
    if "-h" in argv or "--help" in argv:
        print(help_text())
        return 0
    if "-v" in argv or "--version" in argv:
        print(f"macos-debloat-zh {VERSION}")
        return 0
    if Path(__file__).resolve() == PERSIST_DIR / "debloat.py":
        return cmd_persist_run()
    if any(arg in argv for arg in ('--memory', '--memory-ui', '--reclaim-memory', '--memory-report')):
        return cmd_memory(argv)
    options = {"--preset", "--dry-run", "--attempt-protected", "--status", "--verify", "--json",
               "--list", "--audit", "--restore", "--enable-all", "--disable-all", "--report",
               "--disable-sip", "--enable-sip"}
    unknown = [arg for arg in argv if arg.startswith('-') and arg not in options]
    if unknown:
        print("未知参数：" + ', '.join(unknown) + "；--help 查看用法", file=sys.stderr)
        return 1
    if "--dry-run" in argv and any(arg in argv for arg in ("--restore", "--disable-sip", "--enable-sip")):
        print("--dry-run 支持服务预设及目录启用/禁用；恢复和 SIP 操作不能与它组合。", file=sys.stderr)
        return 1
    if "--report" in argv:
        return cmd_report("--json" in argv)

    major = macos_major()
    if major is not None and major not in (26, 27):
        print(f"当前为 macOS {major}；目录针对 26 / 27，请先使用 --audit 查看适用项。",
              file=sys.stderr)

    sections, source_note, absent, version_skip = load_sections()
    if not sections:
        print("没有适用于本机的服务", file=sys.stderr)
        return 1

    dry_run = "--dry-run" in argv
    as_json = "--json" in argv
    is_sip_on = is_sip_enabled()
    lock_sip_rows(sections, is_sip_on and "--attempt-protected" not in argv)

    if "--disable-sip" in argv or "--enable-sip" in argv:
        is_on = "--enable-sip" in argv
        if is_on == is_sip_on:
            print(f"SIP 已{'开启' if is_on else '关闭'}")
            return 0
        if not is_on:
            print(f"{SIP_COST}。csrutil 将要求管理员认证。")
        code = set_sip(is_on)
        print(recovery_steps(is_on) if code else "重启后生效")
        return code

    preset_name = ""
    if "--preset" in argv:
        i = argv.index("--preset")
        if i + 1 >= len(argv) or argv[i + 1].startswith("-"):
            print(f"--preset 需要预设名：{', '.join(BUILTIN_PRESETS)}；自定义目录：{PRESETS_DIR}", file=sys.stderr)
            return 1
        preset_name = argv[i + 1]

    if "--audit" in argv:
        return cmd_audit(sections, absent, version_skip)
    if "--status" in argv or "--verify" in argv:
        return cmd_status(sections, as_json)
    if "--list" in argv:
        listed = sections
        if preset_name:
            preset = resolve_preset(sections, preset_name)
            if preset is None:
                print(f"未知预设 '{preset_name}'", file=sys.stderr)
                return 1
            if not any(sec.items for sec in preset):
                print(f"预设 '{preset_name}' 没有服务", file=sys.stderr)
                return 1
            listed = preset
        print(format_labels(listed), end="")
        return 0
    if "--restore" in argv:
        index = argv.index("--restore")
        candidate = argv[index + 1] if index + 1 < len(argv) and not argv[index + 1].startswith("-") else None
        return cmd_restore(sections, Path(candidate) if candidate else None)
    if "--enable-all" in argv or "--disable-all" in argv:
        refresh_state(sections)
        want_enabled = "--enable-all" in argv
        if not want_enabled:
            print("关闭整个目录包含 AirDrop、应用搜索、账号认证、商店及更新安装依赖。")
        for sec in sections:
            for it in sec.items:
                it.selected = want_enabled
        kept = keep_locked_on(sections)
        if kept:
            print(f"跳过 {kept} 项 SIP 受限服务；--attempt-protected 只允许尝试，不绕过保护。")
        return run_apply(sections, dry_run)
    if not preset_name and argv == ["--dry-run"]:
        preset_name = EXTREME_PRESET
    if preset_name:
        return cmd_preset(sections, preset_name, dry_run)

    print(f"macOS 精简 {VERSION}；本机 {sum(len(sec.items) for sec in sections)} 项")
    if absent:
        print(f"已跳过本机不存在的 {len(absent)} 项；--audit 查看详情")
    if version_skip:
        print(f"已跳过不适用于当前系统版本的 {len(version_skip)} 项")
    if not reopen_tty_stdin():
        print("交互界面需要终端；也可以使用 --preset、--dry-run、--status 等命令。", file=sys.stderr)
        return 1
    refresh_state(sections)
    result = curses.wrapper(run_tui, sections)
    if result:
        print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
