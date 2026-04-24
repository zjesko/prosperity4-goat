# Wiki

# **Algorithmic trading challenge: “Options Require Decisions”**

There are 2 ‘asset classes’ in the three products you trade. The `HYDROGEL_PACK` and `VELVETFRUIT_EXTRACT` are “delta 1” products, similar to the products in the tutorial and rounds 1 and 2. The 10 `VELVETFRUIT_EXTRACT_VOUCHER` products (each with a different strike price) are options, and thus follow different dynamics. All products are traded independently, even though the price of `VELVETFRUIT_EXTRACT_VOUCHER` might be related to that of `VELVETFRUIT_EXTRACT` due to the nature of options.

The vouchers are labeled `VEV_4000`, `VEV_4500`, `VEV_5000`, `VEV_5100`, `VEV_5200`, `VEV_5300`, `VEV_5400`, `VEV_5500`, `VEV_6000`, `VEV_6500`, where VEV stands for **V**elvetfruit **E**xtract **V**oucher, and the number represents the strike price. They all have a 7-day expiration deadline starting from round 1, where each round represents 1 day. Thus, the ‘time till expiry’ (TTE) is 7 days at the start of round 1 (TTE=7d), 6 days at the start of round 2, 5 days at the start of round 3, and so on.

The position limits ([see the Position Limits page for extra context and troubleshooting](https://imc-prosperity.notion.site/writing-an-algorithm-in-python#328e8453a09380cfb53edaa112e960a9)) are:

- `HYDROGEL_PACK`: 200
- `VELVETFRUIT_EXTRACT`: 200
- `VELVETFRUIT_EXTRACT_VOUCHER`: 300 for each of the 10 vouchers.

<aside>
📃

**Example**: `VEV_5000` is an option on the underlying VEV with a strike price of 5000 and a position limit of 300. At the start of the final simulation of Round 3, its time to expiry (TTE) is 5 days. In the historical data, the corresponding TTE values are:

- TTE=8d at the start of historical day 0 (coinciding with the tutorial round),
- TTE=7d at the start of historical day 1 (coinciding with Round 1),
- TTE=6d at the start of historical day 2 (coinciding with Round 2).
</aside>

Vouchers cannot be exercised before their expiry, and inventory does not carry over into the next round. Like in previous rounds, any open positions are automatically liquidated against a hidden fair value at the end of the round.

# Implied Volatility and Moneyness

Options pricing. The Black-Scholes formula. Now, this takes me back, dear. I remember when that came out. Caused quite a stir. People acted as if markets had never been uncertain before. As if we had all just been guessing until then. Well. Some of us had been, I suppose.  To assess the real potential of the Velvetfruit Extract Vouchers, you will need to look beyond their prices and examine what sits underneath. The Black-Scholes formula lets you compute the Implied Volatility embedded in each voucher. That number tells you something important. Not what the market knows, but what it expects. Or fears. Often both at once.  Once you have those volatility figures, look at how they vary across strikes and time. Do they move consistently, or does the picture become uneven as you scan across the range? Then bring in moneyness. The relationship between each voucher's strike price and the current underlying price. How does that difference relate to the volatility you calculated?  If you plot Implied Volatility against moneyness, a pattern tends to emerge. The market is always saying something with that pattern, whether it intends to or not. Now, I once had a student who spent three weeks staring at exactly this kind of chart on a portable DVD player. Tiny screen. Terrible glare. She still spotted the pattern before anyone else in the room. Sharp girl. I wonder what became of her. Anyway.  Your job, before you do anything else, is to understand what structure the market is currently implying. Everything else follows from that.

# Positioning With IV and Moneyness

Once you have your Implied Volatility and moneyness figures side by side, the real question becomes: does the picture hold together? Does the distribution look consistent across the board, or do certain vouchers sit somewhere they probably should not?  Markets are not perfectly efficient. Never have been, and I have had a very long time to verify that personally. There are always points that deviate from the broader structure. A voucher that implies more uncertainty than its neighbors. Another that seems oddly calm given where it sits relative to the underlying. Those deviations are worth paying attention to.  Ask yourself what they mean. Is the market overestimating uncertainty in some cases? Underestimating it in others? Both happen more often than people admit. I have seen entire trading floors miss exactly this kind of signal because they were too busy looking at the headline numbers to notice what the structure was quietly suggesting.  Finding the odd ones out is only the first step, of course. Deviation does not automatically mean opportunity. It means something is misaligned. Whether that misalignment is worth acting on, and in which direction, is the judgment call that separates a trader from someone who is simply guessing, sweetheart.  Buy, sell, or stay put. The structure will give you clues. But the decision, as always, remains yours.

# Let's Talk Volume

Ah. Volume. The part where confidence meets honesty. And they do not always get along, in my experience.  You have identified a mispriced voucher. Possibly two. Possibly three. Possibly more. Now the question is not whether to act, but how much to commit. And that question deserves more thought than it usually gets.  Consider the magnitude of the deviation you spotted. A voucher that appears slightly misaligned is a different proposition from one that seems significantly off. Does that difference justify proportional exposure? In principle, yes. A stronger signal tends to warrant a larger position. But principle and practice have a complicated relationship, as any experienced trader will tell you.  Larger volume amplifies your returns if you are right. It amplifies your losses if you are not. And the uncomfortable truth is that even a well-reasoned volatility interpretation can be wrong. Markets have a long history of remaining misaligned far longer than anyone expects, and moving in directions nobody anticipated.  That reminds me... I have a full breakdown of exactly that phenomenon, recorded during a particularly turbulent stretch in a market... I know it is stored on a MiniDisc here somewhere. Oh boy, how am I supposed to find anything if I can’t even read my own handwriting from back then?! You know what? I’ll let you know once I found it, okay? But the lesson was clear enough that I remember it without it. Conviction is not certainty.  So before you commit, ask yourself honestly: how confident are you in your reading? And how much damage can your strategy absorb if that reading turns out to be off? Volume should reflect the strength of your conviction, love. Not the size of your hope.