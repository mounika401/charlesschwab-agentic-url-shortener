CHANGE REQUEST CR-17 (product) + BUG-101 (support)

CR-17: Marketing wants analytics per short link: total clicks, clicks per day,
top referrers and unique visitors, available from the management API. We must
not store anything that identifies a person (legal says no raw IP addresses).

BUG-101: "Expired links still count clicks." Customer reported that a campaign
link that expired last week keeps showing new clicks in the dashboard even
though visitors get a 410 page. The counter must only move on successful
redirects.

Constraints: existing API responses must not change (clients depend on them);
the existing database must be upgraded in place on deploy.
