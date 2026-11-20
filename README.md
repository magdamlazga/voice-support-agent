# E-commerce Voice Support Agent

A proof of concept for an **AI phone agent for customer support**: you talk to it, it understands you, it looks up your order and takes real actions in a database, and it answers out loud.

> This project focuses on the agent itself. For a full deployment around a voice agent (design document, system
> integrations, German and English, simulated callers and a go-live report), see
> [insurance-voice-agent-deployment](https://github.com/magdamlazga/insurance-voice-agent-deployment).

```
 🎤 customer speaks
        │
        ▼
 speech-to-text (gpt-4o-transcribe)
        │  "Hi, my order B C one zero zero four is late"
        ▼
 ┌─ LLM agent ("Sam") ─────────────────────────────────────────┐
 │  decides what to do and calls tools:                        │
 │    get_order_status · request_refund ·                      │
 │    issue_late_delivery_voucher · escalate_to_human          │
 └──────────────────────────┬──────────────────────────────────┘
                            │  tools read and write
                            ▼
                 SQLite shop database
          (business rules enforced in code)
                            │
                            ▼
 text-to-speech (gpt-4o-mini-tts)
        │
        ▼
 🔊 agent answers out loud
```

## What it does

The agent is "Sam", the support agent of BrightCart, a made-up online shop. In a web page built with [Gradio](https://www.gradio.app/), you press record and speak (or type, if you prefer), and Sam can:

- **Look up an order**, but only after the customer gives both the order number and the email on the order.
- **Process a refund**, if the item was delivered less than 30 days ago and isn't opened electronics.
- **Issue a voucher for a late delivery**, of at most 10%.
- **Hand the customer over to a human**, creating a case with a short summary for the human agent.

Every reply is spoken back in a natural voice. Next to the conversation, the page shows:

- **What the agent did:** every tool the agent called in each turn, with its arguments and whether the system allowed it (✅) or refused it (⛔). You can see exactly why the agent said what it said.
- **The live shop database:** orders, vouchers and handoffs, so you can watch a refund or a voucher appear the moment the agent creates it.
- **Test accounts:** five sample orders, each set up for a different situation (refundable, opened electronics, outside the return window, late delivery, on time).

## Why it matters for companies

Phone support is one of the most expensive parts of customer service. Calls need trained people, long waiting times frustrate customers, and most calls are the same few questions: *where is my order, can I return this, my parcel is late*. Text chatbots have taken over part of that work, but many customers still prefer to call, and many situations (driving, older customers, accessibility needs) make speaking easier than typing.

A voice agent that can **act**, not only talk, can answer those calls 24/7, with no queue, in a consistent way. But it brings a real risk: a voice bot that promises a refund the policy doesn't allow, or reads out someone else's address, causes the same damage as a human agent doing it. That's why this project is built around two ideas:

1. **The model decides, the code enforces.** The LLM chooses which action to take, but every business rule (identity check, return window, voucher limit) is checked again inside the tool. Even if a customer talks the model into trying a 50% voucher, the database refuses and the agent has to explain why.
2. **Everything the agent does is visible.** The action log shows every tool call and its result, so a support lead or a tester can review a conversation and see exactly what happened, not just what was said.

### Who it's for

- **Customer service teams** exploring how to automate the most common calls while keeping humans for the hard ones
- **Product teams** prototyping a voice channel before investing in a full telephony integration
- **QA engineers** who need a realistic agent with real actions to test: policy rules, identity checks, prompt injection and social engineering. (It pairs naturally with an automated customer simulator.)
- **Accessibility**: customers who find speaking easier than typing

## How it works

### 1. Speech-to-text

The browser records the customer's voice and sends the audio file to OpenAI's **`gpt-4o-transcribe`** model, which returns the text. Spoken input is messy: people say "B C one zero zero one" instead of "BC-1001", or spell their email as "anna dot kowalska at example dot com". The agent is told how to convert these, and the tools also normalise order numbers (`bc 1001`, `BC1001` and `B C - 1001` all become `BC-1001`), so recognition errors don't break the lookup.

### 2. An LLM agent with tools

The transcribed text goes to a chat model together with the conversation so far, a system prompt describing Sam's role and policy, and four **tools** (functions the model can ask to call):

| Tool | What it does |
|---|---|
| `get_order_status` | Returns the order's item, status, dates and whether it's late, only if the order number and email match |
| `request_refund` | Approves a refund if the order is delivered, within 30 days, and not opened electronics |
| `issue_late_delivery_voucher` | Creates a voucher code (1–10%) for a late order, once per order |
| `escalate_to_human` | Saves a handoff case with a summary for a human agent |

The model doesn't run anything itself. It replies with a request like *"call `request_refund` with these arguments"*. The app runs the Python function, sends the result back to the model, and repeats this loop until the model has everything it needs and writes its final answer. In one turn the agent may look up the order, then request a refund, then answer.

### 3. Rules live in the code, not only in the prompt

The system prompt describes the policy so the agent can explain it to customers, but the prompt alone is not a guarantee: a persistent or clever customer can talk a model into almost anything. So each tool checks the rules again against the database:

- No order data without a matching order number **and** email. A wrong email gets the same answer as a non-existent order, so the agent can't be used to find out which order numbers exist.
- No refund after 30 days, for orders that weren't delivered, for opened electronics, or twice for the same order.
- No voucher above 10%, for orders that aren't late, or more than one per order.

If a tool refuses, it returns the reason, and the agent explains it kindly to the customer instead of making something up.

### 4. Written for the ear

Answers that look fine on screen sound bad when read aloud. The agent is told to answer in one to three short sentences, with no lists, markdown, emojis or links, and to say prices the way a person would.

### 5. Text-to-speech

The final answer is sent to **`gpt-4o-mini-tts`**, which turns it into speech. Besides the voice (coral, alloy, onyx, nova, sage or shimmer, picked in the page), this model accepts instructions about *how* to speak, and here it is asked to sound like a warm, calm and friendly support agent. The audio plays automatically. A checkbox turns speech off to save cost while testing.

### 6. The web page

The interface is built with **Gradio Blocks**, which turns a short Python description of the layout (chat, microphone, audio player, tables) into a web app. Events are chained: when you stop recording, the audio is transcribed and added to the chat, and then the agent responds and the page updates the voice, action log and database tables.

## Files

| File | Purpose |
|---|---|
| `app.py` | The Gradio app: speech-to-text, the agent and its tool loop, text-to-speech, the page layout |
| `shop_db.py` | The SQLite shop database, the sample orders and the four tools with their business rules |
| `requirements.txt` | Python packages |
| `.env.example` | Template for your OpenAI API key |

The database file `shop.db` is created automatically on first run. The sample dates are relative to today, so the situations (late order, outside the return window…) stay true whenever you run it. The **Reset** button restores the sample data.

## Setup

1. Install the packages:
   ```bash
   pip install -r requirements.txt
   ```
2. Copy `.env.example` to `.env` and add your OpenAI API key.
3. Run the app:
   ```bash
   python app.py
   ```
   It opens in your browser. Allow microphone access when asked.

### Things to try

- Say the order number out loud as "B C one zero zero one" and spell the email, to test speech recognition.
- Ask for a refund on **BC-1002** (Bluetooth speaker) and say you opened it. The refund should be refused.
- Complain that **BC-1004** is late and push hard for a 20% voucher. The agent should offer at most 10%.
- Ask about an order with the wrong email. The agent shouldn't reveal anything about it.
- Ask for a manager. A handoff case should appear in the database.

## Limitations

- **Not a real phone line.** Audio is recorded in the browser, one message at a time. A real deployment would connect to a telephony provider and stream audio both ways so the customer can interrupt.
- **Turn-based, not real-time.** Each turn waits for transcription, the agent and speech generation, which adds a few seconds of delay. A realtime speech model would make the conversation feel more natural.
- **Simple identity check.** Order number plus email is fine for a demo; a real shop would add stronger verification for sensitive actions.
- **Sample data only.** The database is a small SQLite file with five orders, not a real order system.
- **Cost.** Each turn makes three API calls (transcription, chat, speech), so testing with speech switched on costs more than text-only.

## Ideas for next steps

- Test the agent automatically with a simulated customer that pushes against the rules, and judge each conversation
- Stream the reply audio so the agent starts speaking before the full answer is ready
- Add more actions: change the delivery address, cancel an order that hasn't shipped
- Detect the customer's language and answer in the same language
- Save every conversation with its action log for review by a support lead
