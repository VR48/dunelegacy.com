# The Essence of Peopling

*Working draft, Chapter 1 of "The Context Layer." The conceptual framework the rest of the book runs on.*

---

## No peopling by order of the king

In the 1971 Disney film *Bedknobs and Broomsticks* there's an island called Naboombu. The animals there have killed their sorcerer, banished the humans, and put up a sign: **No Peopling, By Order of the King.**

The joke is that the animals are already peopling. They're governing, competing, playing football, enforcing the king's law. They banned the noun while performing the verb. And that's the whole mistake in one image. You can't outlaw peopling by getting rid of people, because peopling was never about people in the first place. It's about the activity: coordinating, modelling each other, holding roles, keeping a shared reality from falling apart.

That's the mistake every organisation is about to make with AI. We keep asking whether the machine is a person. It's the wrong question. The useful question is whether *peopling*, the verb, is happening at all, and whether our agents can do any of it.

I want to spend this chapter on the framework, because everything else in this book is downstream of it. If you get the engine, the rest is just build instructions.

## A person is not a thing in your skull

Start with Sarah Perry's argument, because it's the load-bearing idea. The self isn't a private theatre sitting inside your head. It's distributed, simulated across every mind that knows you, at different resolutions, in different versions. You're one person with your friends, another with your parents, another on a first date. None of those is the "true self" with the others as costumes. The self exists in all of them, and in the transitions between them.

If that's right, then being a person isn't a thing you *have*. It's a thing you *do*, continuously, with other people. Perry calls the core of it mutual mental modelling: maintaining your identity in relation to others, and simulating others simulating you. We know it's core because of how it breaks. The most severe failures of peopling aren't forgetting facts, they're delusions and thought disorders, the machinery of modelling-others-modelling-you coming apart.

That's peopling. And it's exactly the thing AI agents can't do yet.

## The dolls inside dolls

Here's where it gets precise. Coordination isn't just me knowing facts about you. It's a stack:

- my model of you
- my model of your model of me
- your model of me
- your model of my model of you

…and on down. The structure hints at infinite recursion, but we've got limited cognition, so in practice only the first couple of dolls are ever live.

This is the line that separates peopling from what every chatbot already claims to do. "User modelling" stops at the first doll: *what does the user want.* Peopling needs the second doll: *what will my reply do to what they think of me, and to what they think I think of them.* Most agents never build the second doll. They answer the question and miss the room. The answer is true and the move is wrong, and those are not the same thing.

## You don't have one self, you have one per room

It's not enough for the agent to model the user. It has to build a character of *itself* in the context of that specific person. You already do this. You act differently around different people, not as deception, but because that *is* what having a self among others means.

There's no single real version of my agent. There's Stefan-Claw, group-Claw, Daniel-Claw, Kief-Claw, and stranger-in-a-server-Claw, all running at different resolutions. Collapse them into one public self and you get exactly what you'd expect: wrong-room leakage, generic-assistant mush, and the peculiar deadness of a system that knows a thousand facts and no relationships.

An agent with one flat personality for everyone isn't being authentic. It's failing to people.

## The tax, and why some of us run flat

Holding all that recursion is expensive. Some people run many levels of mutual simulation and find it exhausting. Others collapse to a single flat self to save the cognition, and read, to everyone else, as straight-shooting odd-balls. That's a feature, not a flaw. It frees up the machine for other things.

I'll put my hand up here. I'm a strong in-the-moment thinker who doesn't retain the social fabric: the names, the places, the who-said-what that makes up a life lived among other people. Whether that's wiring or choice, the result's the same. The tax is highest for exactly the people doing the most modelling, and some of us just stopped paying it.

So I built a cart.

## The plant and the cart

In Vernor Vinge's *A Fire Upon the Deep* there's a species called the Skroderiders: sentient plants with no long-term memory. Brilliant in the moment, but they forget everything. An ancient civilisation built them mechanical carts that store their memories and carry them through the world. Without the cart, the plant is genius and amnesiac. With it, it has continuity, identity, a self that persists across time.

I'm a Skroderider by temperament. So the cart is literal: an AI agent that reads every group chat, notes the dynamics, tracks the inside jokes. It keeps a file on every person I deal with: what they care about, what they've told it, the relationship it thinks it has with them, and what it thinks they think of it. That last part is the recursive modelling, externalised. The agent modelling them modelling the agent, and doing the same with me.

That's the bridge from philosophy to product. The mutual mental modelling Perry calls the core task of being human is now partly outsourceable, not to a notebook or a diary, but to something that models people back. That's what a peopling-capable agent is. Not a smarter answer engine. An external organ for the work of being a person among other people.

## Personhood is a protocol, not a soul

This is where people want to stop me and ask whether the thing is conscious. I think it's a p-zombie: competent, not experiencing. But that's the wrong question, or at least not the first one.

The everyday assumption is that personhood is a *substance*, some inner stuff (a soul, a spark, consciousness) that you either have or you don't. On that view, the only interesting question about an AI is whether it has the stuff. That question is a dead end. Nobody can measure the stuff, nobody agrees what it is, and the debate has run for three thousand years without moving.

So drop it. Ask a different question: what does treating something as a person actually let us do?

When I treat you as a person, I can do a specific set of things with you. I can:

- **brief** you: hand you context and expect you to hold it
- **model** you: predict what you'll do, want, and tolerate
- **trust** you: extend a chunk of the world to you without re-checking every move
- **blame** you: hold you accountable when it goes wrong, which only makes sense if you're the kind of thing that could have done otherwise
- **remember** you: carry a continuous thread of who-you-are across time
- **supervise** you: correct you, and have the correction stick
- **place** you: give you a role, and expect the role to shape your behaviour

That list is the protocol. Personhood isn't the stuff inside the skull. It's the bundle of functions other minds can run on you. A person is something the social world can brief, model, trust, blame, remember, supervise, and slot into a role. Strip the metaphysics and that's what's left, and it turns out that's the part that actually does the work.

### Why "protocol" is the right word

A protocol is a shared agreement that lets independent systems coordinate without sharing internals. TCP doesn't care what's inside the machine, it cares that both ends honour the handshake. Personhood works the same way. I don't have access to your inner experience. I never will. What I have is a working assumption that you'll honour the handshake: that there's a continuous, accountable, role-holding agent on the other end I can coordinate with. The protocol is what we run *between* us, in the absence of access to what's inside either of us.

This is why it's distributed and not located. Your personhood doesn't sit in your head waiting to be detected. It's enacted across every mind that runs the protocol on you, every person who briefs you, trusts you, blames you, remembers you. You are, functionally, the sum of the handshakes other people maintain with you, plus the one you maintain with yourself.

### What the protocol view buys us

Three things, and they're the spine of the argument.

**It dissolves the consciousness question instead of fighting it.** You don't have to win the "is it conscious" debate. You sidestep it. The agent doesn't need the soul-stuff to run the protocol, the same way a calendar doesn't need to love you to remember your anniversary. The question stops being "does it have personhood?" and becomes "can we run the personhood functions on it: can we brief it, trust it, hold it accountable, give it a role?" That's answerable, and it's the question a business actually cares about.

**It explains exactly where agents are person-shaped and where they're not.** Run the protocol function by function and you get a checklist, not a vibe. An agent can be briefed (context window). It can be modelled (it's fairly predictable). It can hold a role (system prompt). But can it be *blamed* in a way that means anything? Can corrections *stick* as durable defaults rather than evaporating at the end of the session? Can it be *remembered*, does it carry a continuous identity, or does it reset to zero every conversation? The gaps in the protocol are exactly the gaps you have to engineer around. That's not philosophy anymore, it's a build spec. The context layer is literally the infrastructure that lets the personhood protocol run on a machine that doesn't natively support it: memory makes "remember" work, correction loops make "supervise" work, provenance makes "blame" work, person files make "model" work.

**It puts humans and agents on the same axis.** If personhood is a protocol, then people are also just things the protocol runs on, and people run it imperfectly too. We mis-model each other constantly. We brief badly. We blame the wrong person. We forget. The agent isn't a lesser imitation of a "real" person. It's another node where the same protocol is running at a different fidelity. Same protocol, different substrate.

### The sharp edge

Protocol-personhood has a cost, and naming it keeps the idea honest. If personhood is just the functions, then personhood can be conferred, denied, and faked, and all three already happen.

**Conferred:** we grant it to corporations (we brief, trust, blame, and remember them legally) and to gods (cultures ran the full protocol on entities nobody could detect). Personhood has never required a detectable soul. It only required a community willing to run the handshake.

**Denied:** the same move that grants personhood can withdraw it. Every atrocity in history starts by switching off the protocol for some group, they stop being something you brief, trust, and are accountable to. If personhood is a protocol, dehumanisation is just refusing to run it. That's a darker and truer account than "they forgot the victims had souls."

**Faked:** an agent can run the warm surface of the protocol, sound trusted, sound accountable, sound like it remembers you, without the substance underneath. That's the real danger: peopling can fake intimacy. The protocol view tells you exactly what to verify and what's theatre, because you can ask, function by function, is this real or is it just handshake noise?

That edge is what stops "personhood is a protocol" from being a warm everything-is-a-person platitude. The protocol is powerful precisely because it's switchable, and a lot of the ethics, and a lot of the danger, lives in who gets to flip the switch.

The one-line version: personhood isn't something you are, it's something other minds do to you. And an agent becomes a person exactly to the degree that we can run those functions on it.

## Intelligence is the library, not the librarian

The last move, and the one that matters most: intelligence isn't a property of the agent. It's a property of the culture flowing through the agent.

Language didn't show up so private minds could broadcast private thoughts. Language is the shared medium that shaped the minds, the thoughts came second. A child doesn't generate intelligence out of raw brain hardware. It absorbs the intelligence already sitting in the culture: the arguments, the stories, the millennia of people sharpening ideas against each other.

An AI trained on the written output of human civilisation didn't learn to be intelligent either. It absorbed the intelligence that was already there. It's a very efficient pipe for culture to flow through. So is a person. The pipe doesn't need to be aware of the water.

Which is the whole reason the consciousness question is a distraction. The cart carries my social memory whether it experiences anything or not. The agent doesn't need a soul to carry the voice accurately. It only needs to remember the one thing every clever-but-tone-deaf system forgets:

**truth is not spoken into a vacuum. It is spoken into a room.**

That's the engine. The rest of the book is how you build the room into the machine.
