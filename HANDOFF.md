# Handoff

## Gold DON draft content complete — Codex, 2026-11-07

Completed and DB read-back verified: update
existing c5cc700e draft title and description to Gold DON Heroines Edition Vol. 2
Maid Outfits English, preserving photos, quantity and current price. Snapshot and
audit before/with correction; no marketplace publish. Base 4693034; tracked clean,
untracked operational backups preserved. Saved title is 70 characters (under 80).
Current owner price was AUD89.99, preserved with quantity1 and both photos.
Backup don-gold-content-20261107.json and audit saved. No code change or publish.

## DON back attached; pins staged — Codex, 2026-11-07

Completed the checkpointed media intake on nl-vault-label-design after 9058085.
DON draft c5cc700e-ec8a-44ae-ad1e-d7da1d743565 now has its original front and the
owner's actual white/green back. Price and quantity unchanged; no publication.
Actual image lives in listing_photos/owner-upload-b748aa4f/don-actual-back.jpg;
shared future DON back asset also saved separately under representative-backs.
Backup: backups/don-actual-back-20261107.json. Audit event recorded.
Pins are one review record, not new inventory: pair
fbb3593e-c3f5-5a6b-9c21-20fda7b333ce, batch bc7c74d7-b542-511a-8e55-691a8faab361.
Both photos show the same five pins (Johto trio, Squirtle-style, Arceus, two Togepi);
second photo is another front angle. Lot versus individual choice remains pending
from asynchronous question; condition and price also need review. Collectibles
remain Vinted-first. No duplicate stock or marketplace listings created.
Authenticated production HTTPS verified 200 for actual DON back image and pin
review page. Data/media-only work; no code tests or restart needed.
Next: await owner's selling-format answer and prepare the appropriate draft(s).

## Actual DON back and Pokemon pins intake — Codex, 2026-11-07

Owneruploaded B748AA4F photos:1actualwhite/greenDONback;2and3samefivepins (Johto
startertrio1,Squirtle-style1,Arceus1,Togepi2). Askedlotvsindividual asynchronously.
Planattachphoto1 toexistingDONdraftc5cc700e andoriginalpair; preservefront/price/qty.
SavecopyinsharedDONbackslot forpreviouslyauthorizedfuturefront-only workflow, butthis
listingusesactualbackpath,norepresentativeclaim. Stagepins asone collectiblephoto-review
recordwith2images,5visiblepieces, sellingformat/condition/pricepending; no duplicate stock
and no eBaypublication (collectibles previouslyVinted-first). Existingpinsearchfoundnone.
Trackedtreeclean,previousbackups preserved.


## DON corrected; awaiting owner back photo — Codex, 2026-11-07

Deployedcf76d18/source798175f;22tests passed,restartdone. Draftc5cc700e nowOnePiece
DON!!,frontonly; removedwrongPokemonback+disclosure; price0qty1preserved.
Backupbackups/don-correction-20261107.json andaudit saved; StudioHTTPS200 verified.
User nowwillphotograph actualDONback. Attach tosameexistingdraft (no newinventory/listing)
whenuploaded; no whiteplaceholderasset currently exists.
Userreferences read throughTradingGetItem (publicweb403):227594843721 activeAUD85
BandaiOnePieceDON!!YourTurn+1000NamiHoloFoilEnglish; frontvisuallymatch,backwhite/green.
920087689365 activeGBP37.79 title EB05GoldDONHEROINES EDITIONVOL.2MaidOutfits.
These are askingprices, notsoldcomparables. No priceschanged; no thirdpartyphotosreused.
Referencephotos temporary /tmp/don-reference-{0,1}.jpg only. Ownerbackphotopending.


## Deploy DON fix — Codex, 2026-11-07

Isolated798175f passed22tests. Nextcopy4ownedfiles,restartCRM, thenauditcorrect
exactunpublisheddraftc5cc700e gameOnePiece/rarityDON!! andremovePokemonsharedback.
NoapprovedwhiteDONphotoyet; preservefront,price,qty. SnapshotbeforeDBwrites.


## DON back correction — Codex, 2026-11-07

Owner flags DON whiteback vsPokemon. Visuallyverified frontphoto ofdraft
c5cc700e-ec8a-44ae-ad1e-d7da1d743565: OnePiece DON!! goldborder YourTurn+1000.
Misclassifiedlocalcatalogpokemon; noebaylink. SharedbacklibraryhasPokemonJP/EN and
OnePieceblue/red, no whiteDON. Nextcorrectexactdraftgame/removeincorrectsharedback
(anditsnotice),preservefront/price/qty. Add DON-specific sharedbackslot that returnsNone
untilone-piece-don.jpg available; prevent DON!! manualcatalogue defaultingtopokemon.
No fakewhitephoto; ownerwillneedupload. Isolatedbranch/tests, deployrestart.
Trackedtreeclean; previouslocalbackups preserved.


## eBay revenue complete — Codex, 2026-11-07

Deployed fc10c0e (source9717e8d);43focused tests passed; ezbay restarted.
Read-only eBaygetOrders returned32orders,allalreadyimported; refreshed37existingfinancial
snapshots with auditedbackup backups/revenue-order-snapshots-20261107.json (0600,localonly).
No eBaywrites, no inventorychanges, no neworders created. Initialinline scriptSyntaxError
occurred beforeexecution; correctedrun completed. Two cancelled/unpaid nowexcluded;
30includedpaidorders,AUD911.65grossimportedhistory since24Aug; November6orders176.66;
combinedmonth201.16includingVinted24.50; today0. Figures beforefees/refunds,includepostage.
HTML eBayrevenuepanel attop withhistorycoverage andlinktoorders. Existingprofitcards
labelledpartial/excludingimportedeBay; no profitmanufactured. Futureorderpollupdates
financialsnapshot evenifstockalreadyapplied; regressionprovesno seconddecrement.
ProductionHTTPS verified200 with176.66,201.16,911.65,coverage andtopposition.
ExistingbackupJSON remainsuntracked; no needfurtherwork onrequestedrevenue.


## Deploy eBay revenue — Codex, 2026-11-07

Isolatedautomation-revenue implementationpassed43tests. Copy sixownedsource/testfiles
(revenuehelper,analytics,orderimporter andthree tests), commit,restartCRM. Thenreadremote
orders torefresh existingfinancialsnapshots only (backup/audit,noinventorychanges).
Productiontrackedfilesclean beforecopy; localuntrackedbackups preserved.


## eBay revenue integration — Codex, 2026-11-07

Owner explicitly requests revenue. Current analytics sums only Sale (oneVinted24.50);
37 importedlines represent32eBayorders, allcurrentlyPAID. Planread-onlyrollup uniqueorderID
using newest snapshot, Sydneycalendar day/month. Grosspaid revenue includesbuyerpostage,
beforefees/refunds, excludescancelled/unpaid; labelledclearly. Include inheadline totals
and separateeBayrevenuepanelnear top; do not imply profit available. Local eBay Sale records
havenoexternalID: whenimports exist useimports as eBaysource (avoid doublecount); clarify.
Isolatedworktree, tests multi-line/cancel/unpaid/currency/monthboundary, deploythenverify.
Productiontrackedclean; existinguntrackedbackupJSONpreserved.


## Analytics stale-page investigation — Codex, 2026-11-07

Owner stillsees No eBay traffic. Re-read DB and liveHTML at both127.0.0.1:8080 and
https://homeserver.tailefaa51.ts.net/analytics with uniquerefreshquery: both200,no-store,
344active listings,32366impressions,1596views,2transactions for06Oct–04Nov.
Traffic fetched2026-11-06T18:44:05UTC. OneCRMsystemdprocess confirmed.
Likely retainedoldbrowserpage, not servermissingdata. Providefreshquery analyticslink
to force navigation; no code/service/data changes warranted by currentevidence.
Separately generic topSales cards useSale table(1row), not imported eBayorders(37rows);
user explicitly clarified missingtraffic, so do not implement revenueanalytics unsolicited.


## Reconnect recovery complete — Codex, 2026-11-07

Deployed f3c0ab1 (isolated source507c16e), restarted ezbay successfully. 46focused
auth/traffic tests passed. LiveHTTPS verified: unsignedHTML /auth/ebay/start ->303login
withpreservednext; loginpage200; APIunsigned401; signedOAuth302auth.ebay.com withanalytics
scope. /analytics200 withtraffic panel. IMPORTANT owneralreadyreconnected: credentials
nowincludeanalyticsscope,344trafficrowsloaded. No furtherreconnectrequired.
Initial smoke assertedReconnectbutton presence andfailedbecauseactualtraffic nowrenders;
followup verified200panel,DBscopeandrows. Not a deploymentfailure.
All26original shared-back revisions: snapshotremoteoffer+GetItemverification passed,
26/26localnoticesconfirmed. Prices/quantity/photospreserved. Newownerpublishing occurred
sincepreviousday; old13pendingcount anddraftstatusnotesareSTALE; re-readbeforefurtherwork.
No remainingimplementation for this request. Localoperational JSONbackups intentionally
untracked; production trackedfilesclean afterthiscommit.


## Deploy reconnect recovery — Codex, 2026-11-07

Worktree /home/nic/automation-reconnect validated:46auth/traffictests passed.
Production trackedfilesclean at7db63d9, operationalJSONbackups untrackedpreserved.
Next copyownedapi.py, analytics.py, tests/test_auth.py, commit andrestartezbay;
verifyloginredirect andOAuthanalyticsrequested onactualhost. No tokenorcookiepolicychanges.


## Reconnect login recovery — Codex, 2026-11-07

All26 original shared-back revisions completed: snapshotreadbacks verify descriptions,
prices/quantities/policies andphotos preserved; original26local notices synchronized.
Many new listings nowpublished byowner since lastturn; do not replaydraftcleanup or assume
old pendingcounts. Scriptsafety changes owned byCodex; localbackups intentionallyuntracked.
User screenshot shows unauthenticated /auth/ebay/start raw401. Confirmed dependency always
returnsJSON401; noHTML loginredirect or returntarget support. Next isolateauthfix worktree:
HTML GET authfail -> login with safe localnext; login resumesreconnect; API401 unchanged.
Tests auth/API redirect and maliciousnext, deployrestart under existingcontinuation authorization.
Do not relax authentication or SameSite; OAuthconsent requiresowner.


## Shared-back continuation — Codex, 2026-11-06

Five live listings verified (Yanma,Zarude,Dudunsparce,Mamoswine,Regirock).
Crobat stopped beforewrite: API alternates eBayPlusIfEligible capitalization even between
consecutive reads. Normalize same knownalias forprewrite compare too; no semanticchange.
Use unique snapshotfilename if prewriteaborted snapshotexists. Resume remaining21.

## Remote read timeout — Codex, 2026-11-06

Resume halted on GetItem read timeout before fourth write; three verified still complete.
Next allow up to3 retries on read-only GetOffer/GetItem calls with45s timeout. Never blindly
retry writes. Existing snapshot verification passed all3, local protectedfields passed39records.

## Shared-back response normalization — Codex, 2026-11-06

Three revised and verified: Yanma, Zarude, Dudunsparce. Next Mamoswine halted beforewrite
because offer description usesCRLF and Trading XML normalizes toLF. Contentidentical.
Normalizeonly CRLF fordescription verification, keep price/quantity/policy/photo assertions.
Resume remaining23. No live changes otherthanapproveddescription noticed.

## Shared-back verification pause — Codex, 2026-11-06

Second remote write Zarude267805604365 succeeded and live description/photos verified.
Strict offer equality stopped batch because eBay changed key casing eBayPlusIfEligible ->
ebayPlusIfEligible (both false), no policy ID/value change. Local notice not yet synchronized.
Next normalize ONLY that known response alias, verify and recover already-current remote
notice into local record with audit; then continue rest. Preserve backup JSONs as local
untracked operational evidence (do not commit database snapshots).

## Shared-back revision first verified — Codex, 2026-11-06

Yanma267805604307 description-only update succeeded. Offer Get/readback non-description
fields identical; Trading GetItem confirms notice and identical photos. Snapshot backups/shared-back-267805604307-20261106.json.
Next run scripts/revise_shared_back_notices_20261106.py --apply forremaining25; script skips
already-current offers, rechecks state, snapshots before, verifies buyer-facing GetItem after.
CRM /shop-listings?category=pokemon and /analytics return200. Pendingphoto counts
remain 7English (6ready,1review),2Japanese,4thirdbatch; do not markreviewcompleted onownerbehalf.

## Codex progress — 2026-11-06

Deployment6f65777 successful: DB backup before migration, Alembic20261106_01, ezbay
and traffic timer active. Analytics credential lacks scope: owner must reconnect /auth/ebay/start.
Owner-approved Vaporeon/Charizard corrections applied atomically and read back; snapshot
backups/printing-corrections-20261106.json, script scripts/correct_printings_20261106.py.
Vaporeon now jp-sv8a-030 (030/187), Radiant Charizard svJL003/021, qty1 each, prices0,
2photos each unchanged. Charizard reference official card45803.
37 unpublished drafts refreshed and audited (backups/multilingual-drafts-20261106.json).
No quantities/prices/photos/catalogue IDs changed. Next batch: remote description-only revisions
for26 current direct active shared-back offers (23old +3new). Read currentoffer, preserveall writable
fields, updateonly listingDescription, verifylive GetItem and offer non-description equality.
First Yanma267805604307 thenrest afterreadback. Save before/after snapshots; current DBfilters
re-read each. No full ListingStudio revise action (it could change title/price/photos).
Earlier plan: repair unpublished multilingual draft names/set codes from already-reviewed photo
facts; no catalogue ID merges. Preserve live records, quantity, price, photos. Then read remote
descriptions and perform approved description-only shared-back notice revisions, one first.


## Codex deployment checkpoint — 2026-11-06

Continuation implementation b8f70d8 / validation1a23203 in automation-card-display.
100 focused tests passed. Production tracked files unchanged from3928d35; inherited
untracked species map preserved byte-for-byte. Next copy owned display/recognition/confirm
files and tests; snapshot DB, migrate, restart, install analytics timer. Owner now explicitly
approves correcting Vaporeon and Radiant Charizard to photographed printings. Snapshot and
audit those exact records, preserve quantities/prices/photos; do not merge other catalogue IDs.
Then clean unpublished titles and execute approved shared-back description-only revisions,
one first with remote verification. Re-read all live states before any record changes.


## Resume Claude card display/deploy — Codex, 2026-11-06

User explicitly says continue where Claude left off. Base50c3f72; production branch
nl-vault-label-design. Only dirty file is Claude's untracked data/pokemon_species_names.json;
preserve original, use a copy in isolated continuation worktree. Plan deterministic Japanese
English display names/set names without moving catalogue IDs, Chinese exact code/number
handling, tests, then approved migration/service/timer deployment and description-only shared
back eBay revisions. Re-read records: owner has been confirming/publishing concurrently.
Codex visual pass of89 original photos is in /tmp/pokemon-review/{identities.txt,matches.json};
DO NOT apply its now-stale matches over Claude/user confirmations. It found Pokeball pattern
Meowth052 and Leafeon002; Meowth currently Master Ball remains owner review, no silent change.
Repeated Nymble/Zeraora/Eevee confirmed separate physical copies by owner in this chat too.


## English titles for JP cards, Chinese card handling, deploy — Claude Code, 2026-11-06

Owner approved ("yes and do what you need to do"): (1) titles/CRM names for Japanese cards in
English (English card + set name, keep "Japanese"), via a JP->EN species name list and JP set-code
English names; drafts/future only, live titles untouched. (2) Simplified Chinese: recognition
prompt for CBB2C-style set codes/4-digit numbers, matching without a Chinese catalogue, fuller
titles. (3) Deploy: alembic upgrade + restart ezbay.service, install ebay-traffic timer
(sudo NOPASSWD root available), then revise the 23 live shared-back listings' descriptions
(owner originally asked for every description; one listing first, then the rest).
Plan order: code 1 -> 2 with tests, commit, deploy, verify, then eBay revisions.

## Listing filter lost after publish — Claude Code, 2026-11-06

Owner: Shop listings Pokémon filter "doesn't stay" and collectibles show up. Request log showed
the flow list(category=pokemon) -> Listing Studio -> publish-to-ebay -> bare /shop-listings.
Fixed: the publish route returns to `_return_prev` when it is a /shop-listings URL (same-site
only). Tests: test_ebay_publish + collectible policy + navigation 97 passed, plus a new HTTP-level
test. Needs ezbay.service restart to go live (owner action).
Open (asked owner): English titles for Japanese cards — official `jp-*` catalogue entries hold
Japanese names/set names only, so titles become Japanese (e.g. ヒトカゲ … ポケモンカード151);
which is also why owner-created `local-*` entries exist. Don't move cards onto `jp-*` entries.
Chinese cards "looked at better" — scope unclear, asked.

## Photo-confirm corrections (owner-approved) — Claude Code, 2026-11-06

Owner answers: Zeraora sv7 109/102 = 2 copies; Eevee V CBB2C 0113 = 2; Nymble 096/094 = 2;
Simplified Chinese cards use the EN back (keep). Owner approved fixing all JP-151-vs-EN mislinks.
APPLIED 08:29Z via `scripts/fix_photo_confirm_conflicts_20261106.py` (audit actor
"claude-code (owner-approved 2026-11-06)"): Charmander 168 + Poliwhirl 176 moved to
jp-sv2a-168/176 (JP titles, JP back); Meowth listing 16508e98 on new jp-sv2a-052
MASTERBALL_PATTERN stock 6b94e506 (owner unsure Poke Ball vs Master Ball — confirm); English
Meowth inv a1f5e9e0 restored to REVERSE_HOLO and left AVAILABLE (owner unsure it exists —
stocktake); Zeraora inv 223cabf4 + listing c3fb97d4 qty 2. Then: Nymble inv 3ae72892 + draft
qty 2; pending Dragonair 3fd7#8 proposal -> jp-sv2a-148. All re-read and verified.
INCIDENT: the first "dry run" committed, because `audit.record_event` calls session.commit().
Script now queues events until --apply. `backups/automation-before-photo-confirm-fixes-20261106.db`
was taken AFTER that commit, so it is not a pre-change copy.
Owner is confirming/publishing in the UI concurrently — re-read rows before any write.
Pending review: Nymble 7a7b#40 -> "Same card, new photo"; Eevee V d51f#8 -> Extra copy qty 2,
d51f#9 -> "Same card, new photo"; Espeon CBB2C 0512, Flareon VMAX CBB2C 0414, Dragonair.
Not in scope without a further OK: ~38 JP cards confirmed onto new `local-*` duplicate catalogue
entries although official `jp-*` entries exist (title quality e.g. "Nexnszoroarkex").

## eBay listing traffic analytics — Claude Code, 2026-11-06

Last updated: Claude Code, 2026-11-06. Owner approved the feature; read-only against eBay.
Done in `6725701`: `sell.analytics.readonly` added to SELLER_SCOPE (existing token keeps its
own scopes on refresh); `automation_control/ebay_traffic.py` (traffic_report, LISTING
dimension, 30-day window ending 2 days back, 200 ids/call, zeros for listings with no row);
table `ebay_listing_traffic` + migration `20261106_01`; `scripts/refresh_ebay_traffic.py`,
`scripts/run-ebay-traffic-refresh.sh`, `docs/systemd/ebay-traffic-refresh.{service,timer}`
(daily 05:41, no name collision on host); "eBay performance" panel on /analytics.
Validation: full suite 2591 passed + 1 expected failure (alembic head pin, now updated; passes);
migration upgrade/downgrade passed on a `.backup` copy of the live DB. Full suite: rerun
running after an intermittent fixture OperationalError in test_vinted_fallback (file passed
3/3 alone); see the next entry for the result.
NOT done (owner actions, ask first): `alembic upgrade head` on the live DB + restart
ezbay.service; install/enable the timer; owner reconnects eBay at /auth/ebay/start (the eBay
developer keyset must allow sell.analytics.readonly — unverified); first manual
`scripts/run-ebay-traffic-refresh.sh` run to confirm the live API response shape.

## Stock-back notice + resume Pokemon front uploads — Claude Code, 2026-11-06

Owner asked: continue the three front-only batches, right backs, and every description
must say the back is a stock photo and to message for a real one.
Done: `d89b6a8` rewords `representative_backs.NOTICE` and `with_notice` swaps the old
wording in place, so CRM drafts and outgoing eBay text (`outgoing_ebay_description`) carry
the new text once ezbay.service runs this code. Backs checked visually: EN/intl blue, JP
modern, One Piece blue standard / red leader — all correct images.
Retry of 7a7b1c8a/3fd7a5b0/d51f20d2 ran as nic (DB-only writes, no auto-apply): 0 failed;
6 READY, 81 NEEDS_REVIEW (mostly no catalogue match for JP/Chinese cards, plus no CRM listing
for new stock), 2 APPLIED by the owner in the UI meanwhile. Unmatched pairs get a back only
once identity is resolved. Some proposed descriptions written by the running (old) service
still hold the old wording; with_notice replaces it on output after restart.
Open owner decisions: (1) restart ezbay.service to deploy; (2) revise the 23 live eBay
listings with shared backs so their descriptions show the new wording — external write, not
done; (3) 6 active + 1 sold Chinese / Simplified Chinese listings and 5 new Simplified Chinese
fronts use or will use the EN back — confirm whether S-Chinese needs its own back image;
(4) review the 81 pairs and new-stock quantities in the batch pages.

## Pokemon front uploads — Codex, 2026-11-06

Read-only review of new batches 7a7b1c8a-10b1-47e1-8a27-970b27ffdc25 (40),
3fd7a5b0-4f32-4e58-ad47-1bf5082c49f9 (40), d51f20d2-2e82-44fc-8681-c87033d1eca8 (9).
All89 front files exist; SHA256 scan across historical pair fronts found no identical-file
duplicates involving new photos. This does NOT exclude same physical card rephotographed.
First six recognized: Charmander ready existing listing; Charmeleon, Charizard ex SVP196,
Bombirdier ex234, Mega Camerupt ex156, Stoutland156 need review. Other83 failed.
One read-only extraction retry confirmed Anthropic400 invalid_request_error: credit balance
is too low. No retry loop; owner needs replenish provider credits before standard recognition
can resume. Six representative backs attached already; remaining pending recognition.
No stock/listing/quantity/marketplace changes, no auto-apply. After credits restored retry failed
pairs in these existing batches, check identity/finish/duplicates and ask new-stock quantities.
Do not upload again or create replacement batches. Preserve Claude storefront feed work.


## NL Vault new-items feed for the Discord bot — Claude Code, 2026-11-06

Branch `nl-vault-label-design` (also what the services run), base `bb8c2dd`; tree clean at start.
Owner wants an #nl-vault-shop Discord feed of new figurines. Adding one read-only route,
`GET /feed/new.json`, to `storefront/vinted_showcase.py` (same router, no app.py change):
live Vinted figurines only, same data the Collectables page shows, no internal fields.
The TCG Sniper bot (`~/tcg-sniper`) reads it as a source. Storefront restart is the owner's;
nothing deployed by this task. Codex's Sonny queue and sleeve work are complete; untouched.

Validation: full pytest passed (pytest's real exit code 0); storefront subset 96 passed. Feed route committed as 23e714a; storefront restart pending (owner).


Sonny queue implementation16e4b28 validated:83 tests passed, two existing Vinted mock warnings. Next deploy only shop_listings_admin.py and its test from isolated worktree, restart CRM and verify nine Sonny records excluded with eight active timers preserved. No database mutations.

Completed Sonny queue separation — Codex2026-11-06: deployed61cc94e, CRM restarted. Live authenticated Studio200 and zero Sonny rows; Vinted200 shows preserved countdowns. Correct final count is NINE active Sonny timers plus ONE sold Dalmatian (initial manual count eight was incorrect). All ten absent from main queue; unchanged inventory, prices, posted_at and fallback data. 83 tests passed. View /collectibles/vinted/live via new Studio link. No external marketplace writes.

## Sonny timer queue separation — Codex, 2026-11-06

Owner asks to move Sonnys out of Listing Studio while awaiting eBay timer. Production clean87e53f0. Found eight active Vinted Sonnys and sold Dalmatian still in unpublished queue. Plan isolated sonny-timer-queue branch: filter only Sonny Angel records matched by inventory to active/sold Vinted and not-listed eBay state; preserve live/pending/failed visibility. Link to Vinted live management. No record, timer, stock, price or marketplace mutations. Test query lifecycle and deploy owned admin file/restart CRM.


Read this at the start of a session; update it before stopping. See the
handoff protocol in `AGENTS.md`.

Sleeve deployment result: owned code copied; three metadata records snapshotted and audited; all three preflights ready, quality100, category183461. API confirms Type required and New1000 supported. Next restart and execute authorized publication, verify live offers. 121 tests passed.

## Sleeve publication completed — Codex, 2026-11-06

Deployed c54e090, restarted ezbay.service. All three existing sleeve listings published
through guarded route on explicit owner instruction. eBay GET offer confirms PUBLISHED /
ACTIVE, AUD4.99 each, qty1, letter policy265296854017 ($4). Live IDs:
- Shining Fates Eevee: 267834764402, offer344762282011.
- Perfect Order Zygarde: 267834764447, offer344762412011.
- Prismatic Evolutions Eevee: 267834764504, offer344762602011.
All local links active/no error; authenticated public Studio pages200 with sleeve fields.
121 regression tests passed; only two existing Vinted AsyncMock warnings. Reviewed metadata
snapshot: Documents/Codex/2026-11-06/sleeve-publish-before.json; audit rows saved.
No other listings modified. Sleeve task complete; no further publication pending here.

## Sleeve deployment checkpoint — Codex, 2026-11-06

Isolated implementation 40c7d10; 121 tests passed (two existing Vinted AsyncMock warnings). Production clean at636d573. Next copy owned sleeve_studio.py, parcel_studio.py, ebay_publish.py and test_sleeve_studio.py; snapshot/seed reviewed metadata for the three existing sleeve listings, restart CRM, verify/preflight and publish under explicit owner instruction. No other products touched.

## Sleeve publishing — Codex, 2026-11-06

Owner explicitlyrequests publishthree sleevepacks nowat4.99each. Correctidentities
photoverified: ShiningFatesEevee,PerfectOrderZygarde,PrismaticEvolutionsEevee;sealed
65eachqty1. Next isolatedbranch from9d56c83 add reviewedACCESSORY sleevehandler
viaexistingStudio facade,category183461verifiedAUtaxonomy,NEW condition,required
aspects andexistingdefaultletterpolicy4AUD. Do notuse deckparcelpolicy/measurements.
Enableonlyreviewedcard-sleeve metadata boundtocatalog. Preserveprices/identities/
photos. Tests then deployownedfiles/restart,seedthreeconfirmedmetadata withaudit/
snapshot, preflightandpublishwithguardedapproval,verifyremotePUBLISHED andprices.
Nootherlistingschanges. Productionclean9d56c83 onClaude branch.

## Sleeve identity correction — Codex, 2026-11-06

Owner correctsPaldeanFates toPerfectOrder, unsurecharacter. Inspected actualimage
edce099aa6c44429a1be23f64ae7dcdc.jpg: greenZygardeCore/Cellart, notMimikyu.
OfficialPokemon PerfectOrderETB productpage confirms65sleeves; ownerconfirmsset.
Next snapshot/correct CatalogItem.name andsameShopListing title/ebaytitle/descriptions
for457891f8-abac-432e-8159-9204a12109eb toPerfectOrderZygarde. PreserveexistingIDs
andforeignkeys toavoidduplicates,4.99price,qty1,sealed65pack,photo,draftstatus.
RecordAccessory green/blackcolor andinventory correctionnote; no eBaywrites.
Onlyone listing/inventory useaffectedcatalog. Productioncleancc84ac4.
PriorMimikyu soldresearch no longer applies; price4.99ownerapproved stays.

Identitycorrection completed/audited andreadbackverified. Samecatalog/inventory/
listingIDs retained, catalogue anddraftcopy nowPerfectOrderZygarde65packsealed;
price4.99,qty1,photo preserved. Beforeimageperfect-order-sleeves-before.json saved
underDocuments/Codex/2026-11-06. LegacycatalogIDcontainsoldsetslug butisopaque;
notduplicated/renamed. No liveeBay change; stilldraft pendingaccessorypublishing.

## Sleeve prices approved — Codex, 2026-11-06

Owner says setallto4.99, referring tothree65-sleevepacks only. Productioncleanee9358d.
Next snapshot andtransactionally update ShopListing prices0->4.99AUD forShiningFates
2893414d-169f-4921-9182-6641c56cb276,PaldeanFates457891f8-abac-432e-8159-9204a12109eb,
Prismatic208d3c33-7578-4822-85e3-48dd151edcb2 withauditrecords. Preservequantity1,
Newsealedcondition,photos anddraftstatus. Verify readback. No code orliveeBay changes
inthispriceupdate; accessorypublishing support remainsoutstanding.

Completed: allthree sleeveprices4.99AUD savedwithAuditEvents andreadbackverified;
quantity/statuspreserved. SnapshotDocuments/Codex/2026-11-06/sleeve-prices-before.json.
No eBay writes. Stilldrafts pendingaccessorypublishing support/postage review.
LastupdatedCodex2026-11-06.

## Sleeve price research — Codex, 2026-11-06

Owner requested soldcomparisons, notpriceapproval. Found matching65-pack soldpages:
ShiningFates US336534565633 displaysUS2.50+4.78shipping,OBO soacceptedpriceuncertain;
Mimikyu AU406967909915 soldOct17 displaysAU10.90 including0.99buyerfee,shippingunknown;
PrismaticETB US376377794607 soldAug10 displaysUS3.99 (4sold), USshippingvariesbylocale.
StronglocalactiveShiningFates134029933941=AU3+2.20letter,184sold buthistoricalsale
pricesunknown. PrismaticAU317889990396 indexedactive8.95+3.40; mixedvariant offers
excludedasexactsoldcomps. Suggested askingprices subjectownerreview: Shining3.99,
Mimikyu7.99,Prismatic6.99 AUD pluspostage; no draftprices changed orpublication.
DirectURLs https://www.ebay.com/itm/336534565633,
https://www.ebay.com.au/itm/406967909915,https://www.ebay.com/itm/376377794607,
https://www.ebay.com.au/itm/134029933941. USprices explicitlynotAUD; noFXconversion.
Next owner choosesprices; accessorypublishing support stillneeded. LastupdatedCodex.

## Promo lot parcel workflow — Codex, 2026-11-06

Owner confirms same packedshipping asdecks:300g20x3x15cm. Priorpublishauthorization
and saved44.99 apply. AUtaxonomy read-only verified183455CCGMixedCardLots,
requiredGame,condition1000New/3000Used. Next isolatedbranch from503b8ab implement
reviewed OTHER-cardlot handler withparcel fields, correctlotcategory/USED condition,
explicitcardcount/holder/aspects, supportedvia sharedparcel facade alongside sealed
workflow. Do notmisclassify catalog asTCGsingle; do not enable arbitraryOTHERitems.
Tests forpreflight/save/publish/duplicate-safe recovery; preserve sealed tests.
Then seedonlyconfirmedlot metadata withsnapshot/audit,deployownedfiles/restart,
publish through existingapproval flow at44.99, verifyremote. No sleeveprices yet.
Productionclean503b8ab onClaude branch. Needhandoff beforeeach mutationbatch.

Promo lot implementation tested inisolatedpromo-lot-publishing ecf8d7d.
115passed22.20s, twoexistingVintedAsyncMock warnings. FinalenumUSED_EXCELLENT maps
verified3000Used inlotcategory183455. Deploy onlyebay_publish.py,newparcel_studio.py,
newcard_lot_studio.py,newtest_card_lot_studio.py afterunchangedbasecheck.
Then snapshot/seed exactlyone reviewedlot record withcatalogbinding,three card
identities,holderincluded,NearMint,300g20x3x15cm; preserveprice44.99/qty1/photos.
RestartCRM,verifyauthenticatedStudio,thenpublish via existingconfirmation under
priorownerrequest. Productioncleansince93b6735; noClaude files touched.

### Promo lot LIVE — Codex, 2026-11-06

Deployedadc837a,restartedCRM,authenticatedpublicStudio200READY quality100. Seeded
reviewedlotPlatformConfig withAuditEvent andsnapshot Documents/Codex/2026-11-06/
promo-lot-before.json. Published viaexistingapprovalworkflow: offer344586587011,
listing267834641143 at44.99AUD. RemoteGETconfirmedPUBLISHED/ACTIVE,sold0,category
183455. Qty1complete3-cardlot+holder,NearMint description,USED_EXCELLENT=3000,
parcel300g20x3x15cm MAILING_BOX withcalculatedAustraliaPostpolicy266481104017.
Noerrors/duplicates. Existingdeck/single/graded/Vinted regressions passed115total;
twoexistingAsyncMock warnings. Isolatedworktreeecf8d7d. Remainingbatch: three
sealed65-sleevepacks prices0; askownerprices orresearchsoldcomparables. No sleeve
publication orpriceassumptions. LastupdatedCodex2026-11-06.

## Removable promo lot identity — Codex, 2026-11-05

Owner confirms three cards are removable fromred/blackholder. Next snapshot
listingb741c128-e2b4-4e5d-8aa6-8942f8c056bc, update title/description tothree-card
promo lot withholder, preserving44.99price,qty1,NearMint,photos. No sealedclaim.
KeepOTHERtype until properlot publishing supported; neverpretend singleTCGcard.
Next review shipping withowner (holder mayrequireparcel ratherthanletter), then
preparelot support and reviewthree sleeveprices. Productionclean17b9ec0.

Promo lot draft updated: three removableEnglishpromos plusred/blackholder,
qty1lot,44.99 unchanged,NearMint,photos unchanged; snapshotpromo-lot-copy-before.json
andAuditEvent saved. Notpublished; OTHERtype stillneedslotpublishing path.
Next owner confirms shipping format/packedsize, thenprepare publication; sleeves
remainunpriced. LastupdatedCodex2026-11-05.

## FS12 retry and remaining product review — Codex, 2026-11-05

Owner explicitly requests publishstarterdeck andgo through others. Next retry
existingFS12 offer342369382011 atapproved145AUD,qty1,300g20x3x15cm, correctedmailing
box anddispatch2229 using guardedworkflow; verifyremoteactive. Then inspectpromo
 display44.99 andthree0-price sleevepacks for next review. No inventedprices or
publishing unsupportedproducts. Productioncleanceff94e; no codechanges planned
in this publicationbatch. Existing snapshots/audits retained; reuseexistingoffer.

FS12 COMPLETE: reusedoffer342369382011, live listing267834008913 at145AUD;
remoteGETverifiedPUBLISHED/ACTIVE,sold0,localACTIVE/noerrors. Remainingreview:
promo display saved44.99 qty1NearMint withfront/back; inspected bothphotos showing
red/black three-card display (079/080/081), no proofsealed. Need ownerconfirm
whether three removablecards inholder orsingleprinteddisplay before choosing
publishingtype. Three sleevessealed65each qty1 prices0. No otherchanges. Next
reviewdisplay first withowner, then sleeveprices andproductpublishing support.
LastupdatedCodex2026-11-05.

## Remaining XL publication — Codex, 2026-11-05

Owner says "All8.99" in response to remaining MegaKangaskhanXL andSkeledirgeXL
prices. Prior publishauthorization applies. Productionclean27718e9. Next snapshot
bothdrafts, save8.99AUD each with audits, preflight andpublish using existing4AUD
letterpolicy, confirm remoteoffersPUBLISHED with prices/IDs. Preserveqty1,NearMint,
twophotos andXLidentity. Zapdosalreadyactive untouched; decks unchanged. No code
changes/restart. Guard against any preexistinglive link before publish.

RemainingXL COMPLETE: MegaKangaskhanXL listing267833789117 offer343187881011;
SkeledirgeXL listing267833789135 offer343187976011. BothremoteGETverifiedPUBLISHED,
listingACTIVE at8.99AUD,sold0; local linksACTIVE/noerrors. Preservedqty1/photos,
4AUDletter policy. Snapshotremaining-xl-before-publish.json underDocuments/Codex/
2026-11-05 plusprice andpublicationaudits. AllthreeXL nowlive8.99each. Noother
changes. LastupdatedCodex2026-11-05.

## Zapdos XL publication — Codex, 2026-11-05

Owner approvesZapdos8.99AUD; prior explicit publication request remains active.
Next snapshot draft566c6464-6019-40d5-800b-04eb1b73b160 price0, save8.99 withaudit,
preflight/publish through guardedworkflow, GETverify remoteoffer. Qty1NearMint,
XL/Jumbo identity and two photos preserved. Existing defaultpolicy265296854017
read-only verified AustraliaPostStandardLetter4AUD plus optionalpickup. Owner
requested large-letter shipping; use existing flatletter service (no invented
parcel measurements). OtherXL prices stillpending. Productionclean88e0b50,
no code changes. Do not create duplicate if existing link becomesactive.

ZapdosXL COMPLETE: saved8.99 withAuditEvent, published existingdraft; remoteoffer
343186983011 confirmedPUBLISHED/ACTIVE listing267833788915 at8.99AUD,sold0.
Postage existing4AUD AustraliaPostletter/pickup policy. Local linkACTIVE,errorNone.
Snapshot Documents/Codex/2026-11-05/zapdos-xl-before-publish.json. No otheritem
changes; Kangaskhan/SkeledirgeXL remainpendingprices. LastupdatedCodex2026-11-05.

## Remaining listings publication — Codex, 2026-11-05

Owner explicitly requested publication of the other starter decks and XL cards.
FS11/ST30 prices130/89.99 approved; owner now confirms same package dimensions
as FS12 (20x3x15cm). Packed weight not explicitly confirmed: async question asks
whether both are300g. Three XL prices remain0; async asks owner prices. Owner says
XL shipping is large-letter size; preserve that preference, do not fabricate
postage weight or prices. Next save only confirmed two deck dimensions after
snapshot with audits. Await required weight/prices before dependent publishing.
Productionclean5e67fd7 on Claude branch, no code changes. Publication authorization
persists; do not ask permission again once required information supplied.

Saved and verified FS11/ST30 dimensions20x3x15cm; weights left blank as before.
Audit records committed with data; snapshot remaining-deck-dimensions-before.json
under Documents/Codex/2026-11-05. No prices/photos/quantities changed and no eBay
writes yet. Awaiting owner weight confirmation and threeXL askingprices.

### Authorized publication execution — Codex, 2026-11-05

Owner now confirms FS11/ST30 each300g packed. XL prices pending sold comparisons;
provide eBayAU sold search links, do not publishXL. Next snapshot two metadata
records, save300g with audit, preflight each and invoke existing confirmed publish
workflow for FS11 at130AUD/ST30 at89.99AUD. This is explicitly authorized by
"Publish the others for me" plus confirmed shipping. Preserve photos/quantity1.
Record returned eBay IDs and verify remote offer published; no duplicate retries.
Production cleanbe8f66b; no code edits or service restart required.

### Publication blocked by dispatch postcode — Codex, 2026-11-05

Saved both300g values transactionally with audit and before snapshot
Documents/Codex/2026-11-05/decks-weight-before.json. Confirmed publish attempts
FS11 offer343183567011 andST30 offer343183643011 FAILED with eBay25016/216007
"Please enter a valid postcode". No live listingIDs returned. Existing offers
retained for safe retry; no freshduplicate offers needed. Read-only GET current
merchant location200: citySydney,stateNSW,countryAU,postalCode missing(None).
Next ask owner dispatchpostcode, update location within publication scope after
before snapshot/checkpoint, retry existing offers. Do not invent Sydney2000.
XL prices remain0; owner requested soldsearch links provided forjumbo049/025/081;
eBay search pages unavailable tobrowsertool, no verified soldprices claimed.
LastupdatedCodex2026-11-05; publication authorization persists.

### Dispatch postcode and retry — Codex, 2026-11-05

Owner supplied2229. Authorized next: GET/snapshot existing merchantlocation,
update only address postalCode to2229 preserving other fields, GET verify,
then retry existing FS11/ST30 offers via confirmed publish workflow at130/89.99.
Audit location update; retain duplicate guards and check remotePUBLISHED status.
No XL publication pendingprices. Productioncleanf6dd95e, no code changes.

### Publication complete — Codex, 2026-11-05

Dispatch postcode2229 saved via update_location_details204 and GETverified;
existing address otherwise preserved. Before snapshotdispatch-location-before.json
under Documents/Codex/2026-11-05 and localAuditEvent recorded. Reused both existing
offers: FS11 live267833788619 atAUD130; ST30 live267833788644 atAUD89.99.
Remote GEToffers verifiedPUBLISHED,listingACTIVE,prices exact,soldQuantity0.
EzBay linksACTIVE with no errors. No duplicates created. XL cards staydraft pending
owner prices; FS12 not retried in this action (owner previously handling it).
No code changes/restart. LastupdatedCodex2026-11-05.

## Publish error recovery — Codex, 2026-11-05

User screenshot405 traced logs: FS12 Studio save succeeded06:01:13; publishPOST
failed502 at06:01:23, eBay25101 InvalidPostagePackage PackageThickEnvelope.
PWA reconnectGET toPOST-only publish endpoint caused405. Existing offer342369382011
failed/unpublished. Next isolated worktree: useMAILING_BOX for sealed decks,
GET publish route redirects safely toStudio (never publishes), sealed API failures
redirect back with escaped error instead of PWA502. Tests mock failures and safe
GET; verify AU package metadata read-only. No automatic retry/publication; preserve
owner-entered dimensions/prices. Primary clean5804b35, Claude branch preserved.

Recovery tests PASSED67 in39.03s in isolatedworktree06337d5. Deployment next:
copy onlyebay_publish.py,sealed_studio.py,test_sealed_studio.py after unchanged-base
check; restartCRM, authenticatedGET verifies redirect/rendering withoutpublish.
No inventory/prices/measurements/marketplace mutations. GeteBayDetails firsttwo
reads returnedXMLerrors; adapter-based retry pending. Official Inventory enums
confirmMAILING_BOX for mailingbox versus rejectedPACKAGE_THICK_ENVELOPE.

Recovery deployed4ec284e; service restart succeeded. Authenticated publicGET to
FS12 publish URL now303 back toStudio200. Saved145AUD and300g20x3x15cm preserved;
outgoingpackage nowMAILING_BOX.67 tests passed, no actualpublicationretry.
Read-onlyGeteBayDetails could not complete (two upstream XML failures then local
adapter-name import error); don't claim live AU metadata validation. Change uses
official Inventory enumMAILING_BOX; actual acceptance remains next owner-confirmed
publish attempt. Existingfailed offer is retained for safe retry. LastupdatedCodex
2026-11-05. Owner can reopenStudio and retry publication; no duplicate createdhere.

## XL promo set correction — Codex, 2026-11-05

Owner asks why XL promos cannot publish. Read-only preflight found zero prices
and absent TcgCard.set_id for three separate jumbo identities. Existing CardSets
svp and mep have the correct promo names. Production clean at661366e on Claude
nl-vault-label-design; no code edits or branch switch needed for bounded data fix.
Next snapshot and link pokemon-jumbo-svp-049/081 tosvp and pokemon-jumbo-mep-025
tomep in one audited transaction. Preserve jumbo identities, explicitXL copy,
photos, quantity and unapproved prices. Recheck preflight for additional issues;
do not publish or invent prices. No tests needed for this reversible data link;
verify exact rows and live Studio response.

Completed XL set links transactionally with three audit events. Snapshot:
Documents/Codex/2026-11-05/xl-promo-set-before.json. All three preflights now
have ONLY price minimum blocker ($0 still pending owner approval), quality92.
Authenticated public Studio pages all200 and missing-set errors gone, XL copy
retained. No prices/photos/quantities changed; no eBay writes or restart.
Next owner enters and saves approved prices, then reviews and confirms publication.
Last updated Codex2026-11-05.

## Sealed deck Studio deployment — Codex, 2026-11-05

Owner wants sealed decks in same review/confirm/publish flow. Implementation in
isolated sealed-listing-studio-20261104 atf86de18.104 tests passed plus final11
sealed tests; two existing Vinted AsyncMock warnings. Current production tree clean
on Claude nl-vault-label-design229bb42; preserve branch/label files. Deploy ONLY
config.py,ebay_publish.py,newsealed_studio.py andtests/test_sealed_studio.py.
Current AU API verified category183457 and New/FactorySealed, required Game/Set;
existing account policy266481104017 calculated AustraliaPost parcels. No live writes.
Next snapshot current three draft records then add sealed_studio metadata language
English and owner-confirmed unopenedcomplete (no invented packedmeasurements).
Approved FS12=145,FS11=130,ST30=89.99,qty1/two photos each unchanged. Restart ezbay
and verify authenticated Studio pages show only missing parcel fields; no raw-card
blockers. No collectible/storefront changes or publication. Snapshot under
/home/nic/Documents/Codex/2026-11-05. Deploy only files unchanged since31a5478.

Deployment files copied; metadata seeded transactionally for exactly three drafts
with audit records. Before-image saved at Documents/Codex/2026-11-05/
sealed-studio-before.json. Prices/quantities/photos unchanged. Next restart service,
then verify live pages and final checks. No external marketplace writes.

### Live verification complete — Codex, 2026-11-05

Deployed09fcce8; ezbay.service restarted successfully and active. Authenticated
public HTTPS GETs all200 for three Studio pages and allsix actual-product images.
Each preflight has exactly one blocker: packed weight/dimensions. Score86 until
measurements saved. All stale raw-TCG/category blockers gone. Prices145/130/89.99,
quantity1 and two photos preserved. No eBay publication or revision performed.
Owner next: open Studio, enter PACKED grams and cm, save, review and explicitly
confirm publication. Three listing IDs: FS12 15ac4264-9c28-41f1-8bde-4eff6c490ead;
FS11 95195d5e-3143-46ef-9d34-08afdf7a27ce; ST30 6335785f-7056-491b-a47c-13ca708d4b3d.
Scope is sealed starter decks only; sleeves and XL promos retain their own workflow.
No changes to Claude labels/collectibles or Vinted. Code ready; measurements remain
owner input. Regression104passed, final sealed11passed; writes mocked. No live
publication claimed. Last updated Codex2026-11-05.

## CRM dashboard outage recovery — Codex, 2026-11-04

User reports blank home-screen app. Reproduced dashboard timeouts; traceback
shows repeated external FX timeouts per holding. Isolated fix cf498f5 at
/home/nic/automation-crm-fx passed24 FX/dashboard tests. Production tree clean;
copy only scan_ingest/fx.py and regression test, restart ezbay to restore access.
Preserve Claude storefront branch and all inventory/marketplace data. No data writes.
Next: authenticate and verify dashboard after restart, record timings.

## Current task — upload workflow navigation (Codex, 2026-11-04)

Branch `upload-workflow-navigation-20261104`, base `8613c99`; clean tree.
User requests the upload destinations in navigation. Planned small edit: rename
Scan & add to Add stock, expose Listing Studio, Graded cards and Vinted preparation
in drawer, add upload/Studio/Vinted desktop shortcuts and correct active states.
Preserve current branch's completed Claude changes. Files: ui.py and existing
navigation checks. Run relevant tests and desktop/mobile rendering checks.
Deploy navigation through existing localhost ezbay service if restart allowed;
no inventory or marketplace writes, no timer installation/activation. Existing
Vinted fallback code is already on disk but restart previously denied for Claude;
report any denial accurately rather than bypassing it.
Implemented: Add stock label; direct Listing Studio, Graded cards, Vinted preparation
links in drawer/palette; three new desktop shortcuts; specific active prefixes and
Listing Studio count badge. Desktop nav hides at1250px so small screens keep usable
search and drawer. Existing navigation tests adjusted for added links/renamedlabel.
Validation PASSED:39 tests (test_nav.py,test_ui.py), git diff --check; Chromium at
390/1024/1440 verified allfourdrawerlinks, activehighlight onallfourroutes andno
horizontaloverflow. Mobile screenshot visually checked. No application data writes.
DEPLOYED 131e43a: ezbay.service restart completed (old process took its configured
90-second stop grace period). Initial connection checks during restart were refused;
final authenticated GETs allPASSED200 at /scan-add,/shop-listings,/graded-cards,
/collectibles/vinted, eachwithallfourupdateddrawerlinks. Serviceactive.
No fallback timer changes or marketplace/data writes. Restart also loads existing
Claude fallback code alreadyonbranch; timer deployment isstillseparate/notdonehere.
Task complete; refresh page toseeupdatedmenu.

## Branded NL Vault address label (Claude Code, 2026-11-04)

Branch `nl-vault-label-design`, base `e4c9c3a`. Operator supplied a mock-up for the address label.
Printer is 1-bit, so red becomes white. Logo panel and swoosh are cropped from the mock-up into
automation_control/label_assets/; its customer details must never be committed. Postage labels are
untouched. Long words wrap or shrink rather than clip. Sender phone removed from the label, per the
mock-up. tests/test_labels.py and test_pick_run.py passed; full suite not rerun, no test print yet.
Next: `ezbay` restart to go live, then operator prints a test label.

## NL Vault Collectables page from live Vinted listings (Claude Code, 2026-11-04)

Branch `storefront-vinted-collectables`, base `6403afe`. Operator chose option A: show live
Vinted figurines with photo, price and a Buy on Vinted link, then restart the site. All 17
are recorded live; second Owl stays unposted on purpose. Keep STOREFRONT_COMING_SOON on so
old ShopListings stay hidden; /shop/collectibles shows ACTIVE VintedListing rows instead and
falls back to coming soon when empty. Nothing is sold through the site. Files:
storefront/vinted_showcase.py, app.py, common.py, tests. Full pytest passed (real exit code).
Checked against production data read-only: 17 cards, kept figures and old listings absent.
`nl-vault` restarted and verified 2026-11-04. Operator then asked for their Vinted invite link on
this page only; added as a labelled line, restart follows. Vinted's AU referral terms do not
address public posting and can suspend abusive use; operator was told. Branch is unmerged;
services run from this checkout.

## Figurine review with the operator, one at a time (Claude Code, 2026-11-04)

Order: Uribou, Labubu, Owl 1, Owl 2, Chicken Leg, Hippo, Ox, Christmas, Jellyfish, two
Monchhichi. Each is confirmed in chat, then linked with `product_photo_review.save`.
Done: Uribou ($14.99, qty 1), Labubu ($29.99, qty 1), Owls (operator chose one card, qty 2, $24.99 each,
both pairs linked to one stock row), Fried Chicken, Snack Series, renamed from Chicken Leg
($24.99, qty 1), Gorilla, Animal Series 4, renamed from Hippo ($24.99, qty 1 assumed), Buffalo,
Animal Series 4, renamed from Ox ($24.99, qty 1 assumed).
Dalmatian, Santa's Little Helper Series ($26.99, qty 1 assumed; operator says the seat is red), Jellyfish.
Yellow Pixel Gamer, Monchhichi Childhood Memories Series ($16.99, qty 1 assumed).
Brown Hair Figure, Monchhichi, no series as the operator could not find it in the lineup ($16.99, qty 1).
Review complete. Operator posts on Vinted; `ezbay` restart and timer install remain theirs.

## Figures kept by the owner (Claude Code, 2026-11-04)

Operator is not selling Kappa, Lily Bell, Polar Bear or Skunk. Moving their stock to
PERSONAL_COLLECTION and withdrawing their EzBay listings (no eBay, Vinted or export rows
exist); drafts stay in the database. Done and read back. Vinted preparation filter is
committed but live only after the next `ezbay` restart, not run by Claude; until then the
four still show there but cannot be posted. Vinted test files passed; full suite not rerun.
First Owl pair now uses the second Owl's photos on the operator's request; old paths are in
its `extracted.previous_photos`. Operator confirmed two physical Owls, so both records stay,
but each still shows the same photos and neither is linked to stock.
Also confirmed 2026-11-04: Chicken Leg, Hippo, Ox $24.99, Christmas figure $44.99, Hippo
series Animal Series 4. The "Mushroom" record is really Sonny Angel Marine Series Jellyfish,
named by the operator, so it is not the Vegetable secret; renamed, confirmed $24.99, quantity 1;
still needs the operator to link stock at /product-photo-review/e5ce61c3-b903-4cca-abb7-928eb43a6aa8.
Comparable: eBay 156732171375, A$26 new in box and foil, active, 0 sold.

## Figurine Vinted prices (Claude Code, 2026-11-04)

Operator approved a higher pricing strategy. 12 `confirmed_price` values written to
`vinted_draft:*` with audit events (`vinted.draft.save`); titles, stock and listings
untouched. Still on hold: Kappa, Polar Bear, two Monchhichi. Operator then approved the fixes,
applied in this batch after a snapshot: Hedgehog to Uribou, series names, stock and listing
condition New to Used on 10 loose figures. Second Owl kept as a separate figure, as the
operator said; priced like the first. Applied and read back; snapshot
backups/automation-before-figure-fixes-20261104.db. Not confirmed, proposals awaiting
operator: Chicken Leg, Hippo, Ox, Christmas gingerbread, Mushroom (possible Vegetable Series
secret, verify before pricing low). All seven p- uploads still lack a stock link. Monitor's
`ezbay` restart at 09:34 2026-11-04 was Codex's; `ezbay-vinted-fallback.timer` still absent.

## Current task — figurine eBay fallback from Vinted (Claude Code, 2026-11-03)

Last updated: Claude Code, 2026-11-03 (Australia/Sydney). Branch
`vinted-ebay-fallback`, base `472dd02` (contains the unmerged graded work).

Operator answers 2026-11-03, superseding the pending questions further down:
- Vinted listing STAYS UP when a figurine moves to eBay.
- Goes to eBay AUTOMATICALLY after 14 days, no per-item OK. This is the operator's
  explicit authorization for this one job to publish; nothing else changes.
- Buyers pay. Small tracked parcel through eBay.

Read-only findings: eBay policy 266481104017 "Tracked Parcel" is CALCULATED AU_Regular,
2 days handling, so every listing needs weight and dimensions. Operator has given no
numbers yet: fail closed and alert, never invent them. No Vinted sale reader exists
(`record_vinted_sale` has only the manual route as caller). Order import runs every
10 minutes. 0 Vinted listings tracked, 11 figurines AVAILABLE.

Double-sale rules to implement: one stock unit backs both listings. eBay sale closes
the Vinted row without decrementing the already reserved unit, and alerts to delete
it from Vinted. Marking sold on Vinted ends the eBay offer. Do not let storefront,
reconciliation or quantity sync treat that reservation as an error or sell it.

Batch 1 committed: `vinted_fallback.py`, publisher branch, stock hook, audit exemption,
Live on Vinted parcel form, `scripts/vinted_ebay_fallback.py`, tests/test_vinted_fallback.py
(18 passed, mocked eBay; no real figurine payload has reached eBay). Reuses each
figure's existing ShopListing (9 ACTIVE, 1 DRAFT, 1 WITHDRAWN today). Storefront hides
collectibles as coming soon. Category 149372, policy 266481104017.
Validation: full pytest passed, read from pytest's real exit code. An earlier run looked
green only because it reported `tail`'s exit code: one outdated wording test had failed.
NOT DEPLOYED: Claude's attempts to install the timer and restart `ezbay` were denied by
the permission classifier, so the operator does both (unit files in deploy/systemd/,
commands in the chat). `ezbay` still runs the code from its last restart and nothing
moves automatically until then. No figurine has reached real eBay, so the first live
move is untested against eBay.
Next after that: operator enters parcel size on /collectibles/vinted/live, then posts.
Branch is unmerged; `ezbay` runs from this checkout, stay on it or merge first.

## Current task — photo audit review COMPLETE (Codex, 2026-11-03)

Last updated: Codex, 2026-11-03 (Australia/Sydney).
Branch `photo-audit-review-20261103`, base `0c1305a`, plan `66bb08f`.
READ-ONLY user request: duplicates and already-updated photos in photo audit.
No application code, stock, listings, dismissals, photo files, audit cache,
marketplace records or services changed. Reports only plus this handoff.

Results: cache from 2026-10-30 06:14 Sydney contains246 findings;117 remain pending
(129 cached dismissed;134 dismissal rows globally). Pending breakdown:
- 23 old inventory photos despite newer linked listing fronts; SHA256-different
  and all23 old/new pairs visually inspected. All23 have Active eBay mirror rows.
- 84 same current listing photo; not automatically wrong/duplicate. Matcher
  suggests a different card game for90 of all117. Plume Fossil079/086 visual
  photo is correctcard/newstand, not suggestedProfessorResearch; finish notreaudited.
- 8 zero-stock/sold entries remain in queue.
- 2 no current inventory-linked listing: Heracross and Vegapunk below.
Duplicate audit across2352 local frontpaths (all readable), SHA256+equalpHash,
identity/variant/language/condition found NO confirmed duplicate stock among
pending117. Shared backs excluded. Eight old/new listingpairs have withdrawnold
plusactive replacement (Alcremie,Kadabra,Wishiwashi,Wugtrio,Glimmora,Sharpedo,
Zamazenta,GotenksFS11-03), not simultaneous active duplicates.

ATTENTION (do not discard/merge without resolving):
- Mega Heracross ex004/094: currentlisting4b055b08-8589-4f11-8b01-78ecd4016879
  eBay267826904031 has2updatedphotos andquantity2 butinventory_item_idNULL.
  Old inventoryd0f64c25-c248-4f9b-a73c-7ac46ce799c0 quantity1LISTED. Matched
  intakepairb7f9cadc-824e-4ad6-a033-7359d86d14d8 stillREADY. Newfront visually
  verifiedPFL004/094. Quantity/linkage needs resolution; not confirmedduplicate.
- VegapunkOP13-112: inventory9fc292ba-48ae-4017-b59d-06a239494b76 qty1AVAILABLE,
  onlywithdrawnlisting8a2cb75f-feff-4886-812a-b899dea021c4. Noownedreplacement or
  currenteBaymirror match found. Keepforreview.

Rootcause: inventory_audit_review._pending checks only cachedpath vs inventory
scan_image_path, ignorescurrentlistingfront andzeroquantity. Rescanalone will
retainoldsource. Futurefix shouldresolvecanonicalcurrentphoto, invalidatereview
onphotochanges, separatesold/withdrawn andretainunresolvedstock/identity.

Evidence: /home/nic/Documents/Codex/2026-11-03/photo-audit-review.md and .json.
Report hasfull23listinglinks and8soldentries. Readqueries/hashcomparisons/visual
inspection completed; tests notneeded (nocodechanges). No fresh eBay API/image
verification: remote status/quantity fromexistingmirror, notsyncguarantee.
Claude alreadyfinishedgradedworkflow/operatorpublished3slabs; do not repeat.

## Current task — graded publisher wiring (Claude Code, 2026-11-03)

Last updated: Claude Code, 2026-11-03 (Australia/Sydney). Branch `graded-cards-ebay`,
not merged to `main`; `ezbay` runs from this checkout.
Took over Codex's interrupted WIP (its session stopped 2026-11-02 12:00 with no
final reply) and committed it with the fixes below.

Done in this batch:
- Wired measured parcel and tracked policy into both publish and revision paths
  via `_graded_publish_fields`, resolved before any approval or link is recorded.
- Fixed: slabs found no eBay category (production has only `tcg_card` mappings,
  lookup used `graded_card`); they now use the card mapping, no data change.
- Fixed: missing `import re` in ebay_publish.py crashed slab payload building.
- Added tests/test_graded_cards.py.

Validation: full `.venv/bin/pytest` passed. Graded payloads are tested
against a mocked adapter only; never sent to eBay, sandbox or production.

Operator answers 2026-11-03: Codex's proposed prices approved, operator reviews
afterwards. "Same packaging and weight as single cards". Restart `ezbay` and create
the three drafts approved. Publishing NOT approved: drafts only, no eBay writes.

Decision: no numeric weight or dimensions exist for single cards anywhere, only the
postage policy, so slabs use the same default policy and send no package data. This
replaces Codex's tracked-parcel gate and its parcel form. Slabs post untracked.
Photos and certificates re-verified by Claude against all six slab images.

Done 2026-11-03: tracked-parcel gate removed, draft script added and dry-run on a
copy, snapshot `backups/automation-before-graded-drafts-20261103.db`, three drafts
created with approved prices via `scripts/prepare_graded_drafts.py`, `ezbay`
restarted on this branch. Full pytest passed before deploy. Slabs pass preflight.
Nothing sent to eBay; no links or exports. Page rendering was checked server-side;
no logged-in browser check was run.

Sold-but-AVAILABLE findings resolved 2026-11-03: operator confirmed both cards sold
with a second copy remaining. Stock was already correct at quantity 1 and was not
changed. Audit and repair script now honour a `listing.remaining_stock_confirmed`
audit event, recorded for both listings. Manual audit run: all checks clean, exit 0.
tests/test_audit_inventory_vs_listings.py passed; full suite not rerun for this.

Commits outside this repo, 2026-11-03: `~/server` branch `feat/hub-control-centre`
has Claude's monitor and hub fixes; older dirty work there is untouched.
`~/trading-bot` timestamp fix committed by the operator as `5d6d09d` on branch
`briefing-timestamps`, not merged to `master`; that branch is checked out. `.git`
there is now all `tradingbot`-owned: use `sudo -u tradingbot git`, never plain sudo.

All three slabs published by the operator 2026-11-03 on the single-card policy:
Venusaur V $24.99 (267831286735), Shroom & Salsa $39.99 (267831287951), Jiren $24.99
(267831288182). /graded-cards now pre-fills the current listing price, so confirming
there no longer reverts a Studio edit; deployed with an `ezbay` restart. Validation:
tests/test_graded_cards.py and tests/test_ebay_publish.py passed (77); full suite
not rerun for this change.

Open from Codex's other task: figurine eBay fallback remains unimplemented, pending
the replace-or-keep, parcel policy and measurement answers recorded below.

Separate host work (`~/server`, not this repo): nginx disabled, hub rebuilt and
started, collector restarted with bind retry. Those edits are uncommitted there,
alongside dirty work that predates this session.

## Current task — three graded cards for eBay (Codex, 2026-11-02)

Branch planned `graded-cards-ebay`, base `0d7c775`; clean tree at task start.
User requests the three previously uploaded slabs sorted for eBay and explicitly
chose researched prices FOR REVIEW. Do not confirm prices or publish until user
reviews proposed prices. Packed slab dimensions/weight requested, unanswered.
Collectibles remain Vinted-first; graded cards are a separate, authorized eBay task.

Read-only findings: all three pairs still NEEDS_REVIEW, no graded inventory.
All six original photos viewed; front/back complete and labels legible:
- Shroom & Salsa BT18-133, CGC Pristine10, cert4245735015, English2022,
  Dawn of the Z-Legends SR; pair30ac06cd-eed8-44e5-bf54-de85593c9118.
- Jiren FB03-009 alternate art SR, Vault Mint9, cert00003575, English2024,
  Raging Roar; pair1872982d-4e4b-4584-8bbc-1d43800a46b7.
- Venusaur V 001/021 sEF, CGC Mint9, cert3813309008, Japanese2020,
  Venusaur VMAX Starter Set; pairf0bbe565-9712-4033-b827-a6335b4c0234.
Same batch b0222d6e-070c-4dff-9c6e-9ef5638f74c2. Certs visually read, online CGC
verification currently403 and Vault429; do not claim independently authenticated.

Plan batch1: add reviewed TcgCard metadata to isolated GRADED_CARD catalogue
records (existing import_collector_export pattern); duplicate guard per cert,
actual photos only. Add graded review/preparation page with proposed vs confirmed
price, Australia sold links, certificate fields and measured parcel inputs.
Generate slab-specific title/description preserving CGC Pristine vs Mint.
Prepare the three as one-copy drafts, price0/unconfirmed; snapshot before any
production writes and audit. Keep raw-card inventory/prices untouched.

Plan batch2 after checkpoint: graded publisher branch with correct2750 condition,
CGC/Other descriptors, grade/cert, two actual photos, canonical available stock,
no duplicate/reserved stock, explicit tracked-parcel policy + packed dimensions.
Read-only API audit found category183454 allowsCGC275015/Other2750123,
grade10=275020/grade9=275022, cert27503 free-textmax20. Vault not listed; preserve
Vault in buyer copy with Other descriptor. Existing default265296854017 is $4
letter; suitable existing tracked policy266481104017 is calculated AU_Regular,
handling2days, needs measured package. No external policy mutation/publication.

Agents graded_publish_audit and graded_research hit usage limits after read-only
reports; root continues implementation/research. No dirty code at checkpoint.
Continuation 2026-11-02: clean tree at e61efdd verified. Root owns new
`graded_cards.py`, review UI, intake metadata/data preparation and registration.
Publisher batch2 is now queued concurrently: helper may edit only ebay_publish.py,
listing_quality.py and graded publisher tests. Implement scoped graded support,
keeping the reviewed-price/parcel gates below; no DB or external writes.
Root coordinates all HANDOFF checkpoints/commits (helpers must not commit).
Research helper remains read-only. Next: implement/test preparation and publisher,
then checkpoint/snapshot the three draft creations and deployment. User prices
and packed measurements still pending; never publish these unconfirmed drafts.

## Current task — save to Vinted copy pack and 14-day eBay fallback (Codex, 2026-11-02)

Last updated: Codex, 2026-11-02 (Australia/Sydney). Operator explicitly requested
this handoff update so Claude can see the changes and continue if needed.
Branch `vinted-save-and-ebay-fallback`, base `295c900`; plan commit `d7bd292`.

Operator: after choosing a character and saving, regenerate title/description,
open a copy-ready Vinted screen, copy all details, download/reference original
photos; if unsold for 2 weeks, list on eBay. This SUPERSEDES the older absolute
Vinted-only rule below, but only after 14 days unsold. Graded cards stay separate.
Pending questions (unanswered): replace Vinted or keep both? Recommended replace.
What figurine parcel postage policy / packed dimensions and weight / buyer pays?
Do not interpret elapsed time as answers. No Vinted status API exists here.

Vinted save/copy/photo workflow DEPLOYED as `6569d2c`; eBay automation still pending:
- `vinted_copy.py` generates title/description from brand/series/character and
  explicit condition, included contents and notes. Copy-all includes confirmed
  AUD price only; never provisional research or inventory quantity as a lot.
- `vinted_prep.py` defaults Save to generation, allows custom wording when
  unticked, preserves previous wording in draft metadata, redirects to the copy
  screen including staged items. Condition/accessory/notes fields, original
  per-photo downloads, Vinted-first text. No stock/catalogue/price changes on
  Save details; price confirmation remains an explicit separate button.
- `vinted_listings.py` has copy-all, per-photo download/open links, packed-parcel
  measurement hint, actual posting-date field and 14-day due date/countdown in
  Sydney time. Backdated day uses end-of-day conservatively. Due state is only a
  REVIEW prompt, NOT automatic eBay publication.
- Final validation: `120 passed` across test_vinted_identity, test_vinted_copy,
  test_vinted_copy_screen, test_vinted_intake, test_vinted_listings,
  test_product_photo_review, test_collectible_reference, test_collectible_ebay_policy.
  `git diff --check` passed. The interrupted agent's changes were reviewed and
  completed by root. Live browser mobile verification also passed (below).
- Custom mode now survives reload/price confirmation and omitted-mode requests.
  Legacy freeform condition/accessory facts are kept custom unless explicitly
  regenerated; current 16 production drafts were read-only checked and all use
  the known generic template, so all default to new generated-copy behavior.
- Original photo downloads are tested byte-for-byte; share-all requires every
  image to load and keeps image MIME/extensions. Clipboard/mobile verification
  passed. Staged items link directly to stock review.
- Catalogue/policy/export UI now describes Vinted-first, 14-day conditional
  fallback. Actual eBay gates remain intact; no category/postage bypass.

All implementation paths below are owned by this task and committed with this update: root vinted_prep.py/test_vinted_identity.py;
helper agent vinted_copy.py/test_vinted_copy.py completed; audit agent changed
vinted_listings.py/test_vinted_copy_screen.py then stopped. Root takes ownership
of finishing these changes. No unrelated dirty files observed. UI wording changes additionally touch collectibles.py,
selling_channels.py and ebay_bulk_export.py.

Actual eBay blockers / next implementation dependency:
- `selling_channels.py` still blocks collectibles; do NOT blanket remove it.
- direct publisher and CSV export only support raw TCG cards, card condition
  descriptors, and category183454. No figurine category/aspects/payload branch.
- no figurine parcel policy configured; do NOT reuse card-letter postage.
- preflight checks ShopListing quantity but not canonical available/reserved
  stock. Must add stock/identity/duplicate guards before enabling figures.
- no safe manual eBay URL-linking endpoint: cached listing refresh omits SKU;
  CSV result import kind=imported is excluded by storefront kind=single logic.
  Order ingestion relies on SKU=ShopListing.id or linked SKU, not item ID.
- last read-only DB audit: 0 tracked Vinted listings, 11 stock-linked figures
  with qty1 AVAILABLE; 16 prep rows including five staged. No live clocks exist.

Deployment and live verification COMPLETE:
- Restarted `ezbay.service` successfully on `6569d2c`; CRM remains 127.0.0.1:8080.
- Mobile Chromium 390x844: 16 forms, all current drafts default to generated
  wording; hidden+checkbox FormData selects the correct custom/generated mode.
  No horizontal overflow or JavaScript errors. Title, description and Copy all
  details buttons copied the matching fields; provisional price was excluded.
- Original Milk Biscuit photo downloaded successfully as Milk-Biscuit-1.jpg;
  photos ZIP succeeded, images loaded, live page returned200. Screenshots reviewed:
  `/home/nic/Documents/Codex/2026-11-02/vinted-save-mobile.png` and
  `/home/nic/Documents/Codex/2026-11-02/vinted-copy-mobile.png`.
- Browser script `/tmp/verify_vinted_copy_mobile.py` only used GETs, unsaved form
  toggles, clipboard tests and downloads. Zero production POSTs; no marketplace
  writes, stock mutations, draft rewrites or price confirmations performed.
- Operator can use https://homeserver.tailefaa51.ts.net/collectibles/vinted.
  Choose identity, review contents, Save details or confirm price -> copy screen.
  After manually posting, paste Vinted link and actual posting date to start the
  14-day clock at /collectibles/vinted/live. Saving a draft alone does not start it.

Outstanding for Claude / next session:
Automatic eBay publication remains UNIMPLEMENTED pending the earlier choices,
figurine category/aspect/payload and parcel configuration, and a factual unsold
check. A due-date display is not auto-publication. Next eBay work must respect
these constraints, reuse canonical stock/listing/SKU, verify no duplicate sales,
and recheck live data before any transfer. No pending operator answers were
received as of this handoff. Vinted copy workflow itself is complete and usable.

## Current task — Vinted brand, series and name fields (Codex, 2026-11-01)

Branch `vinted-identity-fields`, base `61d8eef`, clean working tree at start.
Operator reports that Vinted preparation lacks Brand, Set/Series and Name.
Plan: expose editable fields populated from existing catalogue/intake metadata;
reuse linked collectible reference suggestions; persist these in Vinted drafts
and carry them into the posting helper and download. Keep unknown identity facts
blank/flagged. Preserve physical stock, existing catalogue, photos and prices.
Provide a details-only save so editing identity does not accidentally confirm a
prefilled price. Keep custom title/description text; offer title generation from
identity explicitly. Staged draft identity must remain available during intake
and after linking stock. No eBay or Vinted external writes or new stock here.

Files: vinted_prep.py, vinted_listings.py, product_photo_review.py (prefill only if
needed), focused identity tests. Next: implement, test saving/reloading and staged
handoff, verify mobile page, deploy ezbay.service within ongoing authorization.
No schema change. Read-only agent checking reference widgets and draft fallback.
Implementation complete: explicit Brand, Series / Set and Figure name fields
with linked reference suggestions on each Vinted form; safe details-only save,
explicit price confirmation, optional title-generation button. Identity follows
posting copy fields, ZIP export and intake prefill; stock/catalogue/prices remain
unchanged by details-only saves. Unknown fields can be cleared; missing legacy
form fields preserve existing values. Staged draft data survives stock linkage.
Validation: 63 focused tests passed (vinted_identity, vinted_intake,
vinted_listings, product_photo_review, collectible_reference); git diff --check
passed. Read-only agent review found blank-form handling issue, now fixed and
tested. Deployed `a6ec47c` by restarting ezbay.service. Live Chromium mobile
check (390x844) passed: 16 forms, 48 unique linked reference lists with populated
options, Milk Biscuit brand/series/name prefills, title button changes only its
own form, reload restores unsaved preview, no sideways overflow or JS errors,
and all three identity fields present in the posting helper. Screenshot:
`/home/nic/Documents/Codex/2026-11-01/vinted-identity-mobile.png`.
No production data changes or external marketplace actions. User can refresh
`/collectibles/vinted` and use Save details without changing prices. Unknown
brand/series entries remain flagged for identification. Task complete.

## Current task — review Claude's Vinted work (Codex, 2026-10-31)

Active agent: Codex; branch `collectibles-vinted-preparation`, base `2dccc6b`.
Operator asks to review Claude's completed work, review figurine prices, and
identify the remaining steps to Vinted. Collectibles only, never eBay; graded
cards remain separate. No publication or confirmed-price changes authorized.

Verified live, overriding older deployment notes below: DB revision
`20261031_02`; Vinted tables exist; prep/live/post screens return HTTP 200.
Working tree clean. Claude committed the prior Codex work and completed manual
Vinted posting, stock tracking and collectible reference lists. Sixteen figures
are visible: 11 stock-linked, 5 staged (Boar, Labubu, Owl, two gamer figures).
All referenced photos exist. Zero Vinted draft prices, zero tracked Vinted
listings, zero eBay links/exports/cached SKU matches for these collectibles.
Four figures still use older photos: Kappa, Lily Bell, Skunk, Polar Bear.

This edit batch: save researched provisional AUD asking prices and source/hold
notes in the existing Vinted draft records (never confirmed prices or existing
ShopListing prices), preserving Claude's new Cauliflower stock and its $23.99
price. Audit Vinted code and run focused regression checks; fix only concrete
workflow defects necessary for the handoff, with another checkpoint if needed.
Price batch DONE: 12 provisional prices + 4 holds saved to PlatformConfig.
Existing prices, quantities and confirmed-price fields untouched. Cauliflower
suggestion retains its new $23.99 price. Audit snapshot and proposals are under
`/home/nic/Documents/Codex/2026-10-31/vinted-price-*`.

Next small fix batch: read-only review reproduced acceptance of negative posted
prices; permissive lookalike Vinted URL domains; and qty2/post2/sell1/remove1
leaving one returned unit incorrectly SOLD. Fix vinted_listings.py and its focused
tests (delegated to Codex vinted_research). Show outstanding identity/price holds
on posting screen. No storefront linking expansion: exact Vinted product links
and sale-email import remain outstanding. No live data correction needed as
there are zero tracked Vinted listings. Also fix the audited intake handoff:
collectible confirmation currently returns to the eBay Studio despite Vinted-only
policy. Route collectible saves/repeats to Vinted prep, keep slab destination,
and recognize explicit product_review confirmation when showing low-confidence
scan warnings (root owns product_photo_review.py, vinted_prep.py and new tests).

Fix batch complete: finite/positive monetary validation; known Vinted item
hosts; atomic removal so stale requests cannot restore sold stock; correct
multiple-unit reserved status; review notes on posting screen; collectible
intake returns to Vinted while graded cards retain Studio. New regression tests
cover concurrent stale removal, two-copy lifecycle, invalid numbers/domains,
review notes and intake destinations. Validation: 71 passed in 12.57s for
`test_vinted_listings`, `test_collectible_ebay_policy`, `test_product_photo_review`,
`test_collectible_reference`, `test_vinted_intake`; diff check passed.
Live read-only checks before fix deployment: all 21 photo URLs HTTP200; 16 rows,
12 suggestions/4 holds/0 confirmed, 0 tracked Vinted listings. Review report:
`/home/nic/Documents/Codex/2026-10-31/Vinted-review.md`.
Deployed `5e6cc7a`: ezbay.service restarted successfully; live prep, empty
Vinted tracking, Kappa hold on posting, collectible destination and graded Studio
all HTTP200 with expected content. No migration or storefront restart needed.
Working tree clean after commit. Next operator steps: confirm prices and exact
identities, onboard five staged figures, post manually and paste Vinted links.
Storefront exact item links and automatic sale-email import remain future work;
manual sold/removed tracking is available. Nothing posted externally.

No external listing actions; no browser/login setup. Price/identity confirmation
and manual Vinted publication remain operator steps. Tests are running.


## Current task

Active agent: Claude Code (2026-10-30), branch
`lens-results-and-collectible-vocabulary`, base `22dd1a7`. Codex ran out of
credits mid-session; the operator asked Claude to finish Codex's work, then fix
CRM navigation (saves/uploads should return to the page the operator came
from).

## In progress

1. **DONE — took over Codex's uncommitted work** (operator requests from Codex
   session 2026-10-29 18:25–19:01 UTC; Codex reported each deployed live):
   representative card backs for front-only uploads
   (`representative_backs.py`), listing finish dropdown (`listing_finishes.py`,
   `ShopListing.finish`), and catalogue identification review for unmatched
   photos (`photo_identification_review.py`). 15 paths, untouched since 05:46
   local. Full suite on that tree: 2304 passed.
   - Problem found: `shop_listings.finish` was added to the live DB by an ad-hoc
     script (`scripts/migrations/migrate_listing_finish.py`), not Alembic. Live
     DB has the column but is still at revision `20261023_02`.
   - Committed Codex's work unchanged as `c91d924` (`Agent: Codex`); Alembic
     `20261030_01` replaces the ad-hoc script (`4592dfe`), tested on a fresh DB
     and a live snapshot.
   - Operator action after commit: run `.venv/bin/alembic upgrade head` (a
     no-op that records the revision) before the next CRM restart, or startup
     will refuse the revision mismatch.
2. **DONE — duplicate review (Codex, finished by Claude):** 13 Needs attention entries
   reviewed; report at
   `/home/nic/Documents/Codex/2026-10-30/Needs-attention-duplicate-review.md`.
   Operator answered that Son Gohan UGM5-002 DA is one card photographed twice,
   so one of its two live eBay listings is a duplicate. Operator approved
   (2026-10-30): end eBay item 267792969857 ($2.40, wrongly titled "C"; CRM
   listing `cf0041ed-…`, phantom inventory `8e6b8c1e-…` NORMAL qty 1) with
   `scripts/end_ebay_listing.py`, hide that CRM listing, and set the phantom
   inventory to 0 with an audit note. Keep the $49.95 DA listing
   (`54237f4a-…`, inventory `a965ae85-…`) untouched. Neither had sold.
   Status: DONE 2026-10-30. eBay 267792969857 ended (API success); CRM row
   retired via `scripts/retire_duplicate_ugm5_002_20261030.py` (rehearsed on a
   snapshot first; re-running refuses). The other 12 Needs attention entries
   need no action per Codex's report (8 already withdrawn, 3 live with
   unrecognised `imported` links — a UI classification bug, 1 withdrawn).
3. **CRM navigation fix (done, not yet deployed):** plan below in "Next steps".
   - Batch 1 DONE `ff5c543`: `navigation.redirect_back`, shared ui.py
     script (return params, trail, scroll, toast, Back link); card edits and
     photo audit converted; browser-checked; full suite green.
   - Batch 2 DONE (uploads): flow-origin cookie; card/collectible/accessory
     capture → queue end returns to the upload's starting page; personal
     collection upload uses redirect_back. Full suite 2331 passed.
   - Batch 3 DONE `3c84fd7` (listings): activate/hide/activate-all/edit and
     eBay cleanup dismiss use redirect_back; trail ignores message-only
     params. Browser-checked on the snapshot copy.
   - Batch 4 DONE (pricing): "Mark updated" returns to the list position.
     Full suite 2333 passed.
   - NOT YET LIVE: the running CRM still has the old code. To deploy:
     `.venv/bin/alembic upgrade head` (records 20261030_01; column already
     exists), then restart `ezbay.service`. Needs the operator's OK.
   - Not converted (deliberate forward flows or untouched areas): review
     save-and-next, capture→review, batch status pages, Listing Studio
     self-redirects, eBay export/publish steps, Whatnot, buying, sales,
     retailer goals, market watch. Convert with `redirect_back` if the
     operator reports them.
   - Not browser-tested: uploads (snapshot copy has no identification
     API key); covered by tests in tests/test_navigation.py. Throwaway test
   instance on 127.0.0.1:8091 (snapshot DB at `~/.cache/ezbay-navtest`, no API
   keys) is approved by the operator for browser checks.

## Done

- Recorded eBay orders for listings that have no SKU (14cf2ae)
- Documented the October checkpoint and the public storefront boundary (3e16063)
- Recorded the late-October live eBay operations run (401aa1c)
- Reviewed all 220 existing commits for agent attribution; documented all 41
  without a Claude trailer by theme, with absolute dates and commit references.
- Added shared instructions in `AGENTS.md` and its `CLAUDE.md` include alongside
  this handoff. No tests or services were run for this documentation task.
- Required saved plans before each small edit batch and progress/WIP commits
  afterward, with validation status, operator constraints and exact resume
  actions. Both agents read the same protocol through `AGENTS.md`.
- Added explicit agent trailers for new commits and recovery instructions for
  partially applied work. Existing uncertain history remains unattributed.
- Reviewed the documentation diff and commit scope; `git diff --check` passed.
  Only `AGENTS.md` and `HANDOFF.md` changed in this follow-up. `CLAUDE.md` and
  the Claude work-history subsection remain unchanged. No tests or services ran.

4. **Photo batch af62ce68 (in progress, 2026-10-30):** 26 cards, 6 done, 20
   held. Claude identified all 20 (see chat). Operator approved: (a) link
   photos + set finishes + Listing Studio drafts (no eBay publish); (b) add
   4 Chinese csv10C cards (Dudunsparce ex 179, Mamoswine ex 104, TR Crobat ex
   130, Psyduck 043) to catalogue + inventory; (c) eBay category mapping for
   pokemon_japan; (d) redesign the bulk photo review page. Trubbish, Deerling,
   Ferroseed, Houndour, Seaking are EXTRA copies (new inventory rows; leave
   the 2026-10-27 bulk-lot rows alone).
   - (c) DONE: pokemon_japan → eBay category 183454 added to production.
   - (d) DONE: one-at-a-time confirm flow (`photo_confirm.py`), committed;
     needs `ezbay.service` restart to go live.
   - (a)+(b) IN PROGRESS by the operator on their phone via that flow.
     Chinese Dudunsparce/Mamoswine/Crobat ex already had catalogue entries
     and active listings (set cn-csv10c); only Psyduck 043/222 is new.

5. **Shop ↔ eBay photo sync (2026-10-30):** eBay→shop DONE (`--refresh` on
   scripts/backfill_ebay_listing_photos.py: 265 read, 85 new, 0 missing).
   Image-hash audit of 254 linked live listings: 228 in sync, 20 with no CRM
   photo (shop uses eBay's), 6 where eBay has an older single photo:
   267796015344 Gardevoir, 267805604307 Yanma, 267796015386 Squirtle,
   267813837184 Loki, 267824797652 Dudunsparce, 267824797679 Shiinotic.
   Operator approved revising those six (photos + CRM titles) while KEEPING
   eBay prices: first set CRM price = eBay price for Gardevoir 2.40, Yanma
   6.23, Squirtle 3.39. DONE: all six revised and read back from eBay
   (prices unchanged). Loki needed a Trading API photos-only revise
   (Inventory API updated its inventory item but not the live listing);
   fixed revise_photos' missing XML declaration (`0c4f5dc`). Final audit:
   234/234 in sync, 20 live listings have only eBay photos (shop shows
   those), 0 out of sync. Nightly timer written (docs/systemd/
   ebay-photo-refresh.{service,timer}, ~03:40 Sydney) but NOT installed:
   the agent's permission rules block installing system services, so the
   operator installs it (commands in the commit message of that change).

6. **Duplicate Crobat ex (2026-10-30, operator-approved):** photo_confirm
   matched pair 6 to a duplicate local catalogue entry
   (local-e8908eacae92fc9fc8f2308c, number "130") instead of
   c1f26d7b… (130/222), so "Extra copy" created stock fbeb7db2… and listing
   aa7a892a… → eBay 267827676356 at $3.99, alongside the existing eBay
   267824797612 at $21.95 (listing c7d2c9fb…, stock 67fb27d2…). One physical
   card. Operator chose to END the $21.95 one and keep the $3.99 one.
   Plan: end 267824797612; withdraw c7d2c9fb, stock 67fb27d2 → 0 RETURNED;
   repoint aa7a892a/fbeb7db2/pair to c1f26d7b; delete the duplicate local
   catalogue entry; fix catalogue_guess to prefer the full printed number.
   DONE: eBay 267824797612 ended (cache shows Ended); CRM cleanup applied via
   scripts/retire_duplicate_crobat_20261030.py (kept listing on c1f26d7b with
   original stock 67fb27d2 qty 1; extra stock and duplicate entry deleted).
   photo_confirm fixed (full-number preference, unique stock handling).
   Needs `ezbay.service` restart for the confirm fixes. Note the kept
   listing is AUD 3.99 vs the old 21.95; operator's choice.

7. **Bundle singles (photo batch 7db66a0c, Claude Code session in the
   `remove-items-sale` worktree, 2026-10-29/30, operator-directed):** working
   directly in this checkout on the current branch (no branch switch: the live
   service reads this tree). Already DONE and audited (audit_events actions
   `ebay.listing.end_bundle_now_singles`, `bundle_cards.*`,
   `shop_listing.price_above_ebay_median`; DB backups
   `backups/automation-pre-*-20261029.db`): ended the 4 live bundle listings
   267785473765/67/68/71 (Trading API); added Celebi x2, Hisuian Zorua,
   Greninja ex as DRAFT listings with inventory (new catalogue item
   `tcgp-716205`); linked 10 existing + 10 new inventory rows to the live
   singles; set 13 CRM prices just above the eBay median (CRM only, NOT pushed
   to eBay; old prices in the session scratchpad and the audit events).
   Operator constraints: no eBay push until the operator reviews and presses
   Revise on eBay per listing; Heracross (listing q2 / inventory q1) and
   Tornadus (q5 / q4) stay unlinked until the operator gives quantities.
   - HAZARD found: the batch was retried 2026-10-29 20:58 local, which cleared
     the metadata stored on its pairs and re-proposed prices. Apply / Apply
     all on batch 7db66a0c would overwrite the new listing prices. Mitigation
     this session: set `proposed_price` to NULL on its READY pairs (old values
     kept in the audit event; a retry would re-propose them).
   - Pair 27 (Celebi) now has two catalogue matches (`neo4-106` original,
     `tcgp-716205` 30th Celebration Classic Collection): choose `tcgp-716205`.
     Pair 16 (Gothitelle) reports no match; its live listing uses `tcgp-647306`.
   - DONE (data): storage slots PK-01-001..013 (tray PK-01 is new, named like
     DB-01/OP-01) reserved for the 13 added inventory rows as UNCONFIRMED
     placements on batch 7db66a0c; `batch.storage_tray` set to PK-01. The
     operator confirms "stored" on the batch page, which copies the slot onto
     each inventory row. `proposed_price` cleared on 31 pairs. One audit event,
     `bundle_cards.protect_prices_reserve_storage`, holds the old values. Note:
     the script's dry-run committed (the audit helper commits the session), so
     the rerun with --apply stopped on its own "placement exists" check; the
     resulting state is exactly the intended one.
   - DONE (code, NOT deployed): read-only "Bundle singles" review page
     (`automation_control/bundle_singles.py`: list at `/bundle-singles` and a
     one-card view with Previous/Next at `/bundle-singles/{photo position}`),
     linked from the drawer's Review group (`ADMIN_NAV`, so the drawer is now
     13 links and `tests/test_nav.py` asserts 13), the top bar (hidden on
     phones) and the active-state prefixes in `ui.py`. Router registered in
     `api.py`. Checked read-only against the live DB: 32 cards, 13 reserved
     slots, 2 listings flagged "no inventory". Needs an `ezbay.service`
     restart (operator action); the restart also brings the earlier navigation
     fix (item 3) live, so run `.venv/bin/alembic upgrade head` first.
   - Validation: tests/test_bundle_singles.py (8) + test_nav.py +
     test_dashboard.py: 56 passed. Full suite (`pytest -q`, 2026-10-30, on
     the tree at `7095bad`): 2354 passed in 6m10s.
   - Open for the operator: confirm "stored" once the cards are in PK-01;
     Heracross/Tornadus quantities; Celebi pair 27 catalogue choice
     (`tcgp-716205`); Gothitelle pair 16 has no match; revise prices on eBay
     one listing at a time. The overnight eBay import now covers all 32 cards.

8. **Pick run and print-all labels (Claude Code session
   `ezbay-platform-review`, branch `feat/pick-run` in worktree
   `.claude/worktrees/pick-run`, 2026-10-29/30, operator-requested):**
   - DONE (code): `/ebay-sales/pick-run` lists every open eBay order's lines
     sorted by drawer then slot (`OP-01-009` → drawer OP-01, slot 009), then
     cards with no slot (Pokémon singles live in the bulk drawers), then
     sku-less `item:` lines, then bulk lots. Orders lettered A, B, … oldest
     first. "Print all labels" (`POST /ebay-sales/pick-run/print-labels`)
     prints each open order's label once, skipping any order with a
     `label.print_ebay` audit event, and stops with 502 on the first printer
     error. `print_ebay_label` now records that audit event. "Open orders" is
     one shared definition (`ebay_sales.open_orders`), also used for a new
     `/ebay-sales` nav badge (`NAV_COUNT_HREFS` in `ui.py`). No schema change.
   - Storage, per the operator: 2 rows of 4 three-drawer units; one One Piece
     (OP-01), one Dragon Ball (DB-01), the rest random bulk (Pokémon singles
     included), 3 free. 1,406 available rows have no slot.
   - Rebased onto `04f2342` cleanly. Validation: targeted (pick run, nav,
     navigation, bundle singles, eBay sales, dashboard, order import, labels)
     144 passed. Full suite in the worktree (2026-10-30): 2360 passed, 3
     failed — all in tests/test_representative_backs.py, because the worktree
     has no `scan_ingest_data/media/.../representative-backs/` images (gitignored
     data); the same 3 pass in the live checkout. Gotcha for worktree runs.
   - Deploy: fast-forward this checkout to `feat/pick-run`, tag, restart
     `ezbay.service`. The restart also brings items 3, 4(d), 6 and 7 live.
     Operator approved the pick run deploy; restart pending their OK on that
     wider scope.

7. **Vegito P-065 (2026-10-31):** duplicate audit of all 264 live listings
   found no other doubles; Vegito "Gold Stamped" eBay 267796015378 ($12.85,
   flatbed scan, no visible stamp) vs promo 267821547918 ($4.95). Operator:
   end 267796015378. DONE: ended on eBay; CRM listing 1800c342… withdrawn.
   Stock 2e2bd483… (tcgp-252816 Gold Stamped) retired to 0 RETURNED on
   2026-10-31 at the operator's request: its scan is the regular promo and
   they own no Gold Stamped copy. One Vegito remains (901d2419, eBay
   267821547918, $4.95).
   Open questions to operator: physical counts for Mega Heracross ex me2-4
   (eBay qty 2, stock 1) and Tornadus svp-210 (eBay qty 5, stock 4).

8. **Claude took over 2026-10-31 ~11:30: Codex is out of usage.** DONE:
   Codex's work committed as `516cf29` (test fixed), Claude's Vinted
   posting/tracking as `e526608`; full suite 2400 passed. NOT LIVE until
   `.venv/bin/alembic upgrade head` (20261031_01) + `ezbay.service`
   restart. Vinted Pro isn't offered in Australia (operator). Next: the
   operator's last request to Codex — review the figurine prices (no
   suggested prices were saved; flag Kappa's possible secret variant).
   Earlier note: **⚠ COLLISION 2026-10-31 11:20 — Claude PAUSED.** Codex is working on the
   same Vinted task in this same working tree (branch
   collectibles-vinted-preparation, plan ac34151). Claude's UNCOMMITTED work,
   built on Codex's uncommitted vinted_prep.py, so not committable alone:
   - new: automation_control/vinted_listings.py (post-to-Vinted screen,
     paste-link → VintedListing + atomic stock reservation, live list,
     sold → Sale on 'Vinted' marketplace, remove → stock back),
     migrations/versions/20261031_01_vinted_listings.py, tests/test_vinted_listings.py
     (16 passing).
   - hunks in shared files: models.py (VintedListingStatus + VintedListing,
     before `class ScanSession`), api.py (import + include_router for
     vinted_listings_router after vinted_prep_router), vinted_prep.py
     (`post_link` button per card + 'Live on Vinted' header link),
     tests/test_release_info.py (head 20261031_01).
   - tests/test_collectible_ebay_policy.py (Codex's) has 1 failure in its
     current WIP state; not caused by the hunks above.
   Operator to decide who continues. Gmail has no Vinted emails yet, so the
   sale-email reader waits for a real sample.
   Original plan: **Vinted semi-automation (started 2026-10-31):** operator decisions:
   collectibles only (existing eBay block stays); sold detection from
   Vinted's sale emails; personal account now, maybe Vinted Pro later
   (keep a `source` field so a Pro API can slot in). No bot posting: Vinted
   terms forbid automation on personal accounts. Plan: (1) VintedListing
   table + migration; (2) phone "Post to Vinted" screen: ordered photos
   via share sheet, copy buttons, category/brand/condition hints, paste the
   Vinted URL back → listing recorded, stock reserved; (3) storefront links
   to the exact Vinted item; (4) sale-email parser marks sold + depletes
   stock (needs IMAP app password on the server — operator). Existing
   /collectibles/vinted prep page (Codex) has 0 saved drafts; build on it.

9. **Collectible onboarding lists (2026-10-31):** DONE `a7d9c81`. Linked
   Brand → Series → Character search lists on all three collectible forms,
   backed by collectible_reference (seeded from the catalogue) merged with
   vocabulary.py; every save remembers its combination. NOT LIVE until
   `alembic upgrade head` (20261031_01 + 20261031_02) and restart.
   Official Sonny Angel line-ups added from sonnyangel.com (85 series, 652
   figures, incl. 2018/2019 versions; pick lists only, not Lens matching).
   Catalogue entries to confirm with the operator: Skunk under Animal
   Series 4 (right only if it's the 2018 version; now Series 2); Sheep and
   Duck under unnumbered "Animal Series" (officially Series 2); Polar Bear
   (officially "White Bear": Series 1, or Series 2 in 2018).
   Operator prices collectibles themselves for now.

## Next steps

- CRM navigation fix. Problem: ~213 POST handlers redirect to fixed pages;
  card edits rebuild `back` from sort only (search, filters and scroll lost;
  callers outside `/inventory` are sent to `/inventory`); `ui.brand_header`
  hard-codes "← Dashboard". Operator uses the CRM as an iOS PWA with no browser
  back button. Reproduced on the test copy: card edit from
  `/inventory/cards?sort=name&q=pikachu` lands on `?sort=name` at the top;
  withdraw from `/shop-listings?status=active` lands on `/shop-listings` at the
  top. Design: `ui.py` submit listener adds hidden `_return_to` (current URL)
  to POST forms and remembers scroll; new `navigation.redirect_back()` accepts
  only same-origin relative paths; convert "stay where I was" handlers,
  keep forward flows (save-and-next, create→open, batch status, eBay
  confirm steps); "← Back" header link. Order: card edits + photo audit,
  uploads, listings, pricing. One commit per area.
- Operator to define the remaining lens-results and collectible-vocabulary
  goal; this documentation task does not establish further implementation work.

## Work history by agent

Each agent keeps its own section. Tie summaries to reliable authorship, explicit
`Agent:` or `Co-Authored-By` trailers, or directly recorded work in the current
session. `git log -i --grep='Co-Authored-By: Claude'` lists the commits behind
the Claude section. A shared human author identity alone is not attribution.

### Claude Code (2026-09-08 to 2026-10-29)

Covers the 80 most recent Claude commits. Older ones weren't reviewed because
the disk was full.

- **NL Vault storefront (Sep 8–15):** GSAP motion, split cards/collectibles
  hero, card-collage homepage, background built from the storefront's palette, cart
  "Continue shopping", Whatnot icon, editable shop listings. On Oct 29 it moved
  to public catalogue mode (`STOREFRONT_MODE=catalogue`), which links out to
  eBay/Vinted/Whatnot instead of running checkout.
- **Two-way storefront ↔ eBay stock sync (Sep 10, phases 1–5):** schema, eBay
  write adapter and widened OAuth scope, manual Publish-to-eBay, website sale →
  eBay auto-sync, eBay order polling reflected back onto the site.
- **Personal Collection lane (Sep 9):** collector-export import, card images,
  price column/sort, set-name alias fixes, per-card photo upload, real One
  Piece TCGplayer images and fixes for the wrong printing being picked.
- **Pricing (Sep 10 – Oct 29):** eBay pricing fixes (Nidoran gender symbols,
  parenthetical sub-variants), 2x market-signal cap on single cards, sealed
  product (ETB/booster box) market prices and Deal Check matching, nightly price
  backfill repaired, timer-driven eBay price runs, 473 stock copies made visible
  to pricing, pricing foundation with review-queue bulk save (Oct 6),
  collectible pricing, a price-safety gate and rarity families (Oct 29).
- **Photo recognition and audit (Sep 10–13):** inventory photo audit as a cached
  review page, "wrong card, re-file it", detection of footer-truncated cards,
  Google Lens result parsing with a field vocabulary, Sonny Angel figure naming
  (Flower Series, Cat Life Series), candidate pictures, stock-art fallback.
- **eBay connection and ops (Sep 13–15):** eBay Info row in exports, settings
  page, fix for a token refresh downgrading production, dead background jobs
  surfaced, Trading API listings view (sees relists), eBay Listings dashboard
  widget, 30-day sliding dashboard sessions, release calendar cleanup.
- **Multi-game scan ingest and inventory (Sep 21–22):** Dragon Ball, One
  Piece, Japanese Pokémon and Super Dragon Ball Heroes detection and pricing;
  Dragon Ball Super Divers catalogue; /inventory turned into a two-level tile
  drill-down by game; 303 invisible holdings fixed; collector-app CSV import
  removed; eBay export as one variation listing per set with a 13% fee markup
  for rare/Dragon Ball cards.
- **Labels (Oct 8):** landscape shipping labels on the HotLabel HL-M9.
- **October checkpoint (Oct 29):** committed about three weeks of live,
  previously uncommitted work. That includes the Listing Studio (eBay titles,
  descriptions and photo sets, approved revisions), front/back photo batches,
  bulk-order picking manifests, and the workflows wired into the CRM shell and
  dashboard. It also includes the dated one-off live eBay operation scripts and
  recording eBay orders for listings without a SKU. See the 2026-10-29
  `CHANGELOG.md` entry.

### Codex

No commit predating `7227ba4` can be reliably attributed to Codex. Reviewed all
220 commits across local refs before that handoff commit: every author and
committer is Nic, and no commit message, trailer or author/committer identity
mentions Codex. The 179 commits with a `Co-Authored-By: Claude` trailer are
excluded below.

These themes list all 41 remaining commits as **unattributed**, without claiming
them as Codex work. Dates are absolute commit author dates, spanning 2026-08-24
to 2026-10-03. `CHANGELOG.md` supplies context only; its 2026-09-24 entries were
recorded in the 2026-10-01 baseline commit (`6c776bd`).

- **Early capture and pricing (2026-08-24 to 2026-09-04):** currency and
  PokemonTCG adapters, card rotation/image processing and price review
  (`1c6c3a4`); later reverted the PriceCharting adapter (`a34f3e7`).
- **NL Vault presentation and discovery (2026-09-08 to 2026-09-09):** storefront
  redesigns, hero character strip added then removed, official Pokemon
  department logo, simpler wordmark, technical SEO and breadcrumbs, product
  image fixes, set filters, sorting and a two-column grid (`540f1c8`, `8eba234`,
  `ae70226`, `621befa`, `7a356ec`, `1c014bd`, `65f6f04`, `99c6121`).
- **Photo capture mechanics (2026-09-09 to 2026-10-02):** rembg background
  removal for collectible photos, then shared upload, background-batch and
  review-queue mechanics across card, collectible and accessory capture
  (`64aa4c0`, `6eec260`).
- **NL Vault administration and orders (2026-09-09 to 2026-10-02):** draft
  listing review and activation, storefront health monitoring and launch
  prerequisites, atomic stock reservations, guarded payment/cancellation/refund
  transitions and a transaction drill (`9413105`, `58e00ed`, `b335054`).
- **Platform baseline and release checks (2026-10-01 to 2026-10-02):** platform
  review and roadmap, expanded operational health, application/database revision
  reporting, Alembic baseline and startup schema enforcement, explicit pricing
  coverage, and service-user file/directory permission checks including new
  files (`6c776bd`, `8d38ce6`, `c5d5a61`, `7fdebb4`, `a938018`).
- **Canonical inventory and pricing identity (2026-10-01 to 2026-10-02):**
  explicit catalogue identity in prices and inventory, compatibility links and
  holding uniqueness, plus PokemonPriceTracker credit protection against
  non-Pokemon and zero-stock lookups (`f9918e2`, `635cd8a`, `1a8ad83`).
- **Recovery evidence (2026-10-02):** dashboard backup freshness and restore
  drill health, a scheduled restore drill, and a repeatable historical-release
  rollback rehearsal (`9e87f01`, `64320d1`).
- **Workflow consolidation and retirement (2026-10-02):** navigation aligned
  to daily workflows, one canonical sellable-draft queue, staged retirement of
  unused Goals and generic job/approval writes, and refreshed operator,
  architecture and recovery guides (`dcddb83`, `4e9764d`, `6d37b50`).
- **Market research and purchasing (2026-10-01 to 2026-10-02):** AU/JP
  collectible discovery, alert intake and Retailer Goals captured in the
  baseline; purchase candidates merged into Market Watch; retailer purchases
  linked to lots and inventory; supplier receiving with landed-cost allocation
  and duplicate/refund safeguards (`6c776bd`, `c51c0d2`, `888a658`, `541f7a5`).
- **Whatnot and direct sales (2026-10-02):** inventory-backed show queues,
  reservations and sale/unsold/giveaway outcomes, plus direct-sale stock
  consumption, customer matching, cost snapshots and guarded voids
  (`8ecd322`, `227496f`).
- **Direct eBay publishing (2026-10-02 to 2026-10-03):** production preflight
  and explicit confirmation, retry-safe offer reuse, duplicate-listing guards,
  new-SKU no-offer handling, placeholder-policy rejection and wider OAuth
  consent, independent order-import readiness, and the recorded live Alcremie
  canary validation (`f611f01`, `35e370b`, `7df4f7b`, `4158c90`, `1b0088b`,
  `0377e8b`).
- **eBay variation details and repository hygiene (2026-10-03):** fuller
  number/set-total, printing and rarity labels in variation exports, and
  ignored variation-revision backups (`f6e0b80`, `a5ddd6b`).

Subsequent verified Codex work:

- **Shared instructions and handoff (2026-10-29):** created the shared agent
  instructions and handoff, including the historical attribution audit
  (`7227ba4`, performed in this Codex session); saved the plan for checkpoints
  before edits (`6401344`, carrying `Agent: Codex`).

## Gotchas

- The NL Vault storefront is a bare uvicorn process, not a systemd unit;
  restart it by the PID on its port.
- A separate cleanup of the dashboard/nav UI was in progress in September;
  check with the operator before touching `ui.py` or the nav in `dashboard.py`.
- On 2026-10-29 the machine's disk was full ("No space left on device");
  shell commands and git writes failed until space was freed.
- The Claude photo-recognition/audit bullet is dated Sep 10–13, but its Cat
  Life Series work is commit `3b6d0fb`, dated 2026-09-15. The Claude section is
  preserved unchanged; that theme extends through 2026-09-15.
- Handoff checkpoints rely on both agents following `AGENTS.md`; no timer or
  hook is installed. A sudden usage cutoff may leave the current edit batch
  incomplete. Inspect the diff against the saved plan before resuming; Claude
  Code still needs to be started or resumed by the operator.

## Last updated

Claude Code, 2026-10-30 15:35 AEDT (pick run, item 8)

Last updated for current task: Codex, 2026-10-31.

Last updated for Vinted audit: Codex, 2026-10-31.

Last updated for Vinted identity fields: Codex, 2026-11-01.

Last updated for Vinted copy workflow and conditional eBay fallback: Codex, 2026-11-02.

### Recovery verified — Codex, 2026-11-04

Deployed FX cooldown and AUD bypass in production commit8786fc6; 24 tests passed.
Restart used sudo systemctl after plain systemctl required authentication; completed
normal90s stop grace. Authenticated public HTTPS checks all200: root/dashboard7.2s
cold,1.6s warm; listings0.21s; add-stock immediate. No inventory, prices, uploads or
marketplace changes. Owner should close/reopen home-screen app. Isolated fix and
regression tests: /home/nic/automation-crm-fx. Production remains on Claude's
storefront-vinted-collectables branch with own FX patch committed. Task resolved
server-side; await user confirmation of home-screen app recovery.
