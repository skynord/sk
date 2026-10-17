import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { ChatCard, ChatReply } from "../api/types";
import { TeamCrest } from "../components/TeamCrest";
import { ErrorBox, Loading } from "../components/ui";
import { formatDateTime, pct, signedPct } from "../lib/format";

const POLL_MS = 3000;
const MAX_POLLS = 90;

interface Message {
  id: number;
  role: "user" | "sky";
  text: string;
  cards?: ChatCard[];
  suggestions?: string[];
}

const GREETING =
  "Hi. I read the app's own model output and answer in plain language. " +
  "Ask me what the model fancies today, where it disagrees with the stored odds, " +
  "or how a particular fixture looks.";

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function ChatCardRow({ card }: { card: ChatCard }) {
  return (
    <div className="chat-card">
      <div className="chat-card-head">
        <span className="muted small">{formatDateTime(card.kickoff_utc)}</span>
        {card.league && <span className="faint small"> · {card.league}</span>}
      </div>
      <div className="chat-card-match">
        <TeamCrest name={card.home_team} size={16} />
        {card.home_team} <span className="faint">vs</span>{" "}
        <TeamCrest name={card.away_team} size={16} />
        {card.away_team}
      </div>
      <div className="chat-card-foot">
        <span className="mono">
          {card.pick_label} {pct(card.probability, 0)}
        </span>
        {card.score && <span className="faint">score {card.score}</span>}
        {card.value && (
          <span className={card.value.is_positive_ev ? "mono" : "muted"}>
            EV {signedPct(card.value.expected_value)}
          </span>
        )}
        <Link to={`/matches/${card.match_id}`}>Analyse →</Link>
      </div>
    </div>
  );
}

export default function Ask() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [waitingForModel, setWaitingForModel] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nextId = useRef(1);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const push = useCallback((message: Message) => {
    setMessages((prev) => [...prev, message]);
  }, []);

  useEffect(() => {
    api
      .chat("hello")
      .then((res) => {
        push({ id: nextId.current++, role: "sky", text: res.reply || GREETING });
      })
      .catch(() => {
        push({ id: nextId.current++, role: "sky", text: GREETING });
      });
  }, [push]);

  useEffect(() => {
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [messages, waitingForModel]);

  const waitForBatch = useCallback(async () => {
    for (let i = 0; i < MAX_POLLS; i += 1) {
      await sleep(POLL_MS);
      const res = await api.upcomingPredictions({ days: 14 });
      if (res.status === "ready") return;
      if (res.status === "error") {
        throw new Error(res.error ?? "The model run failed.");
      }
    }
    throw new Error("The model is taking longer than expected. Ask again in a minute.");
  }, []);

  const send = useCallback(
    async (text: string, opts?: { silent?: boolean }) => {
      const question = text.trim();
      if (!question || busy) return;
      if (!opts?.silent) {
        push({ id: nextId.current++, role: "user", text: question });
      }
      setError(null);
      setBusy(true);
      try {
        let res: ChatReply = await api.chat(question);
        if (res.pending) {
          setWaitingForModel(true);
          await waitForBatch();
          res = await api.chat(question);
        }
        push({
          id: nextId.current++,
          role: "sky",
          text: res.reply,
          cards: res.cards?.length ? res.cards : undefined,
          suggestions: res.suggestions,
        });
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setBusy(false);
        setWaitingForModel(false);
      }
    },
    [busy, push, waitForBatch],
  );

  return (
    <div className="chat">
      <div className="page-head">
        <h1>Ask Sky</h1>
        <p>
          Questions answered from the model's own probabilities and the stored odds. Every figure
          below is computed here — nothing is invented, and the app never places a bet.
        </p>
      </div>

      {error && <ErrorBox message={error} />}

      <div className="chat-log" ref={scrollRef}>
        {messages.map((message) => (
          <div key={message.id} className={`msg ${message.role === "user" ? "user" : "sky"}`}>
            <div className="bubble">
              <div className="text">{message.text}</div>
              {message.cards && message.cards.length > 0 && (
                <div className="chat-cards">
                  {message.cards.map((card) => (
                    <ChatCardRow key={card.match_id} card={card} />
                  ))}
                </div>
              )}
              {message.role === "sky" && message.suggestions && message.suggestions.length > 0 && (
                <div className="chips">
                  {message.suggestions.map((suggestion) => (
                    <button
                      key={suggestion}
                      className="chip"
                      onClick={() => void send(suggestion)}
                      disabled={busy}
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
        {waitingForModel && (
          <div className="msg sky">
            <div className="bubble">
              <Loading
                label="Fitting the model…"
                detail="The first run takes a minute or two; later questions are instant."
              />
            </div>
          </div>
        )}
        {busy && !waitingForModel && (
          <div className="msg sky">
            <div className="bubble">
              <span className="muted">Thinking…</span>
            </div>
          </div>
        )}
      </div>

      <form
        className="chat-input"
        onSubmit={(e) => {
          e.preventDefault();
          void send(input);
          setInput("");
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="What can I bet today? …or ask about a team, a competition or a fixture"
          disabled={busy}
          aria-label="Ask a question"
        />
        <button type="submit" disabled={busy || !input.trim()}>
          Send
        </button>
      </form>
    </div>
  );
}
