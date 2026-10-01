# NKC Brain + Voice

The conversational core of the "digital NKC sir": Claude answers as Nand Kishore Chaudhary, grounded in his public writings, and ElevenLabs speaks the answer in his cloned voice. The 3D avatar plugs into this later; it only needs the text stream and the audio this already produces.

```
question ─► /api/chat ─► Claude (persona + knowledge base, prompt-cached) ─► streamed text
                                                                            │ split into sentences
                                                    /api/tts ◄──────────────┘
                                                        └─► ElevenLabs (cloned voice) ─► MP3 ─► browser plays in order
```

## What's inside

| Path | What it is |
|---|---|
| `knowledge/` | His life, timeline, family, values, quotes, 11 of his own essays (verbatim), and company and weaver facts. Every file lists its source URLs. |
| `persona/persona.md` | System prompt: who he is, how he speaks, and the honesty rules (no invented facts, hedge old figures, no politics, says it is an AI if asked). |
| `persona/eval_questions.md` | 25 test questions (facts, philosophy, advice, guardrails, Hindi/Hinglish) with a scoring guide. |
| `app/brain.py` | Loads the knowledge base and calls Claude with streaming. The full KB (~10k tokens) is sent and cached each turn. It switches automatically to keyword retrieval if the KB grows past about 75k tokens. |
| `app/voice.py` | ElevenLabs streaming TTS. |
| `app/server.py` | FastAPI: `/api/chat` (SSE text), `/api/tts` (MP3), `/api/status`, and the web UI. |
| `web/index.html` | Chat UI: the NK Chaudhary orb with a live waveform, plus a voice switch (**NK Chaudhary voice / Machine voice / Text only**). It speaks each sentence as soon as it is complete. |

## 1. Run the brain (text only)

You need Python 3.10+ and an Anthropic API key (console.anthropic.com → API Keys).

```bash
cd nkc-brain
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                     # then paste ANTHROPIC_API_KEY into .env
uvicorn app.server:app --port 8000                       # open http://localhost:8000
```

To chat in the terminal instead, run `python -m app.brain`. To run the offline tests (no keys needed), run `pytest -q`.

`CLAUDE_MODEL` defaults to `claude-sonnet-4-5`. If the console lists a newer Sonnet, put its model ID in `.env`.

**Then run the 25 questions in `persona/eval_questions.md`.** When an answer is off, fix the cause: a wrong fact means editing `knowledge/`, and the wrong tone means editing `persona/persona.md`. Restart the server after each change.

## 2. Add more knowledge (makes the biggest difference)

Drop any `.md` or `.txt` content into `knowledge/` as a new `.md` file and restart. Put the source at the top. The most valuable additions, in order:

1. **LinkedIn.** LinkedIn blocks automated reading, so open his profile, choose **More → Save to PDF**, and paste the text into `knowledge/05_linkedin.md`.
2. **Talk and interview transcripts** (YouTube auto-captions, the Leadermorphosis podcast, TEDx). These carry his real spoken style.
3. **His e-books** from nkchaudhary.com/publications.
4. **A recorded interview with him** (the questions in `eval_questions.md` work well as prompts). The transcript goes here, and the same audio trains the voice, so one session serves both.

## 3. Create his voice in ElevenLabs

> **Consent first.** A Professional Voice Clone has to be verified by the speaker himself, so NKC sir needs to be part of this step. Clone only with his (or the family's) written go-ahead.

### Which clone type

| | Instant Voice Clone (IVC) | Professional Voice Clone (PVC) (recommended) |
|---|---|---|
| Audio needed | 1–2 minutes | 30 minutes minimum, 1–3 hours for the best results |
| Time | Ready in seconds | Trains for a few hours after upload |
| Likeness | Close, good for a first demo | Very close, holds his accent and Hindi/English switching |
| Plan | Starter and up | Creator and up |

Plan names and limits change, so check elevenlabs.io/pricing. A good path is to make an IVC today to test the whole pipeline, and replace it with a PVC once you have clean audio.

### Record the audio

- **Equipment:** a lapel or USB mic 15–20 cm from his mouth, in a quiet room with soft furnishings and no fan or AC noise. A phone in a quiet room works for the IVC.
- **What he says:**
  1. Reads some of his own essays aloud from `knowledge/03_writings_verbatim.md` (about 20 min).
  2. Answers the `eval_questions.md` prompts freely in English (about 20 min).
  3. Answers 10 minutes of the same prompts in Hindi or Hinglish, if the clone should speak Hindi.
- **Style:** the way he naturally talks to a student, warm and unhurried. The clone copies whatever style is in the samples, so avoid recordings where he is shouting or performing.
- **Clean files:** only his voice (no interviewer, music or applause), no echo, peaks around −6 dB, WAV or MP3 at 192 kbps or higher.
- **Existing talks and interviews** can fill the gap if they are clean. Send them to me and I'll strip the other voices, denoise, and cut them into clips.

### Create the clone

1. Go to elevenlabs.io → **Voices** → **Add a new voice** (or **Create or Clone a Voice**).
2. Choose **Professional Voice Clone** (or **Instant Voice Clone** for the quick test).
3. Name it `NKC`, upload the audio files, and set the language (English, plus Hindi if you recorded Hindi).
4. For a PVC, **verification**: NKC sir reads the on-screen sentence into the mic. This step proves the voice owner consents.
5. Wait for training to finish. You'll get an email or in-app notice.
6. In **My Voices**, open the NKC voice, use the **⋯** menu → **Copy voice ID**.
7. Go to **Developers / API Keys** → **Create API key**. Text-to-speech permission is enough.

### Plug it in

Add the key and voice ID to `.env`:

```
ELEVENLABS_API_KEY=your_key
ELEVENLABS_VOICE_ID=the_voice_id_you_copied
ELEVENLABS_MODEL_ID=eleven_multilingual_v2
```

Restart the server. The header should now show *voice on*, and every reply will be spoken.

### Tune it

| Setting in `.env` | Lower | Higher | Start at |
|---|---|---|---|
| `ELEVENLABS_STABILITY` | More expressive, can wander | Steadier, can sound flat | 0.5 |
| `ELEVENLABS_SIMILARITY` | Smoother, less like him | Closer to him, also copies noise in the samples | 0.8 |
| `ELEVENLABS_STYLE` | Neutral | More of his mannerisms, slower | 0.1–0.2 |
| `ELEVENLABS_MODEL_ID` | `eleven_flash_v2_5` is fastest (use it for live conversation) | `eleven_multilingual_v2` gives the best quality | multilingual_v2 for now |

If a name is mispronounced (Churu, Manchaha, bunkar sakhi), add it to a **Pronunciation Dictionary** in ElevenLabs, or spell it phonetically in `knowledge/`.

## Voice options and fallback

- **NK Chaudhary voice** is the cloned voice via ElevenLabs. The ● next to it means it can't be used right now. Hover over it to see why: the key or voice ID is missing, the key was rejected, the voice ID wasn't found, or ElevenLabs isn't reachable.
- **Machine voice** is the computer's built-in text-to-speech. It works offline. It prefers an Indian-English voice, and a Hindi voice for Hindi text. You can pick any installed voice from the dropdown.
- **Text only** turns speech off.
- **Automatic fallback:** with NK Chaudhary voice selected, if ElevenLabs fails (setup, network, quota or API error), that answer is spoken in the machine voice and a notice explains why. The next question checks ElevenLabs again. One answer never mixes the two voices.
- Tapping the orb stops speaking.
- On Windows you can add more machine voices under **Settings → Time & language → Speech → Add voices** (for example English (India) and Hindi).
- `ELEVENLABS_TIMEOUT` (default 8 seconds) sets how long the app waits for ElevenLabs before falling back.

## Conversation mode (voice)

- Press **Start conversation**. He introduces himself and then listens. Talk normally: he replies when you pause, and stops if you talk over him.
- **Reply after** sets how long a pause counts as the end of your turn: about 0.65 s, 0.95 s (default) or 1.5 s. End-of-turn uses both the speech recogniser and the mic level, so he won't cut in while you're still mid-sentence.
- In voice mode the brain answers the way people talk: one to three short sentences, the heart of the answer first, and sometimes a question back. It also corrects misheard words (for example "Jaipur rocks" becomes Jaipur Rugs). If his answer takes a moment, he says a short "Hmm." first. `VOICE_MAX_TOKENS` (default 220) caps the length of spoken answers.
- **Two ears:** the mic level (with the browser's echo cancellation) decides when you start, stop or talk over him, so interrupting works even before any words are recognised. The browser's recogniser writes down your words; if it misses an utterance, the recording is sent to ElevenLabs speech-to-text (Scribe) instead. That needs the **Speech to Text** permission on your API key. `ELEVENLABS_STT_MODEL` defaults to `scribe_v2`.
- **Listen in** switches speech recognition between English (India) and Hindi. Chrome and Edge only, with an internet connection; headphones work best.
- The chat bar only appears in **Text only** mode.

## Conversation history

- Every conversation is saved automatically: your questions (typed or spoken) and his answers, in order, with times. If you interrupt him, the part he had said is kept and marked as interrupted.
- Each **Start conversation** begins a new saved conversation. Typed chats are saved as one conversation per visit to the page.
- Click **History** (top right) to open the history page. It lists conversations by day, with search, and has **Download** (as a .txt file) and **Delete** buttons.
- The files are stored in the `conversations/` folder inside the app, one JSON file per conversation, and never leave your computer. To keep them elsewhere, set `CONVERSATIONS_DIR` in `.env`.

## Hosting on Vercel

Already prepared in this folder: `api/index.py` (entry point), `vercel.json`, `.vercelignore`, `.gitignore`, a passcode lock (`APP_PASSCODE`), and history storage in Upstash Redis when hosted. `.env` and `conversations/` are never uploaded.

1. **GitHub:** create a **private** repository and push this folder (`git init`, `git add .`, `git commit -m "NKC"`, `git remote add origin …`, `git push -u origin main`). `.env` stays on your computer.
2. **Vercel:** Add New → Project → import the repository. Framework preset: **Other**. Leave the build settings empty. Don't deploy yet; first add the variables in step 4.
3. **History storage:** in the project, go to **Storage** (or Marketplace) → **Upstash** → **Redis** → create (free plan) → connect it to this project. Vercel adds `KV_REST_API_URL` and `KV_REST_API_TOKEN` automatically.
4. **Environment variables** (Settings → Environment Variables): `ANTHROPIC_API_KEY`, `CLAUDE_MODEL`, `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`, `ELEVENLABS_MODEL_ID`, `ELEVENLABS_SPEED`, and **`APP_PASSCODE`** (choose one, share it only with people who should use the app).
5. **Deploy.** Open the `….vercel.app` link, enter the passcode, and press Start conversation. Check `/api/status`: `history_storage` should say `upstash-redis` and `voices.nk.available` should be `true`.
6. **Updates:** every `git push` redeploys automatically.

Notes: the mic needs HTTPS, which Vercel provides. Conversations saved on your PC stay on your PC; the hosted app starts with an empty history. Your ElevenLabs plan's parallel-request limit is shared by everyone using the hosted app at once.

## 4. How the avatar connects later

The 3D step only needs what this already produces: the text stream from `/api/chat` and the MP3 audio from `/api/tts`. Lip-sync either reads the audio (Audio2Face) or uses ElevenLabs' character timestamps (the `/with-timestamps` TTS endpoint). The UI here gets replaced by the Three.js scene, and the brain and voice stay as they are.

## Known limits

- The knowledge comes from public web pages. Company figures date from about 2017–2022, and the persona is told to say so.
- Two essays ("love in business" and "illusion of knowledge") are summaries, not his exact words. The persona is told not to quote them as his.
- The model will politely decline anything outside the knowledge base rather than guess. That is intentional: it should never put invented opinions in a real person's mouth.
