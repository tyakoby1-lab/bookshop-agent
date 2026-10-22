# Bookshop.org → Instagram agent

Every 2 days it posts one book from `books.csv` to Instagram:
- **Morning (~8:00 Israel):** book spotlight
- **Evening (~18:00 Israel):** your review (or, for your own book, an author post)

Shop: https://bookshop.org/shop/JewishChildrenBooks (put this link in your Instagram bio)

## How a book moves through the queue

| status | meaning | who acts |
|---|---|---|
| `queued` | waiting to be drafted | agent |
| `needs_notes` | `review_notes` is empty | **you**: write notes, set back to `queued` |
| `needs_image` | no cover found online | **you**: put a cover URL in `image_url`, set to `queued` |
| `drafted` | captions + image ready in `drafts/<isbn>.json` and `images/` | **you**: read, edit, set to `approved` |
| `approved` | will post at the next due cycle | agent |
| `posted` | done | — |

The agent always keeps one draft waiting for you, so you only ever approve one book at a time.
Use `priority` (higher = sooner) to push holiday books forward.

`kind`: `book` = someone else's book (review), `self` = your own book (author post, never a self-review).

## One-time setup (about an hour)

### 1. Instagram
1. Instagram app → Settings → Account type → switch to **Business** or **Creator**.
2. Go to https://developers.facebook.com → **Create app** → use case **Manage messaging & content on Instagram** (Instagram API with Instagram Login).
3. In the app, add your Instagram account as a tester and accept the invite in Instagram (Settings → Website permissions → Apps and websites).
4. Generate an access token with `instagram_business_basic` and `instagram_business_content_publish`.
   Copy the **token** and your **Instagram user ID** shown next to it.
   (Posting only to your own account as an app admin/tester works without App Review.)

### 2. Anthropic API key
https://console.anthropic.com → API keys → create one. Writing 15 books' captions a month costs cents.

### 3. GitHub
1. Create a **public** repository (Instagram downloads the images from it, so it must be public) and upload these files, including `.github/workflows/post.yml`.
2. Repo → Settings → Secrets and variables → Actions → **New repository secret**:
   - `ANTHROPIC_API_KEY`
   - `IG_USER_ID`
   - `IG_ACCESS_TOKEN`
3. Same page → **Variables** tab → add `DRY_RUN` = `1` for your first test.
4. Repo → Settings → Actions → General → Workflow permissions → **Read and write**.

### 4. Test
1. Fill in `review_notes` for a book in `books.csv`.
2. Actions tab → **bookshop-instagram-agent** → **Run workflow**.
3. Check `drafts/` and `images/` in the repo. Edit the captions if you like, then set the book to `approved`.
4. Run again: the log shows what *would* be posted.
5. When happy, change `DRY_RUN` to `0`. From then on it runs by itself.

## Maintenance
- **Token renewal:** Instagram long-lived tokens last 60 days. Every ~50 days run
  `IG_ACCESS_TOKEN=... python agent.py refresh-token` and paste the new token into the GitHub secret.
- **Bookshop lists:** after a book posts, add its ISBN to the matching list (`list_name`) on Bookshop.org.
  Bookshop has no API, so this stays manual (or ask Claude to do it in the browser).
- **Settings** (environment variables): `CYCLE_DAYS` (default 2), `REVIEW_AFTER_HOUR` (15),
  `SHOP_THEME`, `CLAUDE_MODEL`.
