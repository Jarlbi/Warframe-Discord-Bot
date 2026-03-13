import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiosqlite
import aiohttp
import httpx
import random

import os
TOKEN = os.environ.get("TOKEN", "YOUR_TOKEN_HERE")

# Standing reward per completed quest
QUEST_STANDING = 100
QA_STANDING_ANSWER = 50   # answerer gets this later (Step 7)
QA_STANDING_ASK = 10      # asker gets this later (Step 7)

# Rank thresholds — highest first
RANK_TIERS = [
    (10000, "Architect"),
    (7500,  "Executor"),
    (5000,  "Warden"),
    (3500,  "Defender"),
    (1500,  "Initiate"),
    (500,   "Neutral"),
]

def get_rank(standing: int):
    for threshold, name in RANK_TIERS:
        if standing >= threshold:
            return (threshold, name)
    return None

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ── XP / Level helpers ────────────────────────────────────────────

async def award_standing(user_id: int, guild: discord.Guild, amount: int):
    async with aiosqlite.connect("database.db") as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, standing) VALUES (?, 0)",
            (user_id,)
        )
        await db.execute(
            "UPDATE users SET standing = standing + ? WHERE user_id = ?",
            (amount, user_id)
        )
        await db.commit()

        async with db.execute(
            "SELECT standing FROM users WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()

        standing = row[0] if row else 0

    rank_info = get_rank(standing)

    if rank_info:
        member = guild.get_member(user_id)
        if member:
            all_rank_names = [name for _, name in RANK_TIERS]
            roles_to_remove = [r for r in member.roles if r.name in all_rank_names]
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove)
            role = discord.utils.get(guild.roles, name=rank_info[1])
            if role:
                await member.add_roles(role)

    return standing, rank_info

# ── Q&A Modal ─────────────────────────────────────────────────────

class AnswerModal(discord.ui.Modal, title="Submit Your Answer"):
    answer_text = discord.ui.TextInput(
        label="Your Answer",
        style=discord.TextStyle.paragraph,
        placeholder="Type your answer here...",
        max_length=1000
    )

    def __init__(self, qa_id: int, asker_id: int):
        super().__init__()
        self.qa_id = qa_id
        self.asker_id = asker_id

    async def on_submit(self, interaction: discord.Interaction):
        async with aiosqlite.connect("database.db") as db:
            await db.execute(
                "UPDATE qa_posts SET answerer_id = ?, status = 'answered' WHERE qa_id = ?",
                (interaction.user.id, self.qa_id)
            )
            await db.commit()

        await interaction.response.send_message(
            f"💡 **{interaction.user.display_name}** answered:\n> {str(self.answer_text)}"
        )


# ── Q&A View ──────────────────────────────────────────────────────

class QAView(discord.ui.View):
    def __init__(self, asker_id: int, qa_id: int):
        super().__init__(timeout=None)
        self.asker_id = asker_id
        self.qa_id = qa_id

        answer_btn = discord.ui.Button(
            label="💡 Answer",
            style=discord.ButtonStyle.green,
            custom_id=f"qa_answer_{qa_id}"
        )
        answer_btn.callback = self.answer
        self.add_item(answer_btn)

        best_btn = discord.ui.Button(
            label="✅ Mark Best Answer",
            style=discord.ButtonStyle.blurple,
            custom_id=f"qa_best_{qa_id}"
        )
        best_btn.callback = self.mark_best
        self.add_item(best_btn)

    async def answer(self, interaction: discord.Interaction):
        if interaction.user.id == self.asker_id:
            await interaction.response.send_message(
                "❌ You can't answer your own question.", ephemeral=True
            )
            return

        async with aiosqlite.connect("database.db") as db:
            async with db.execute(
                "SELECT status FROM qa_posts WHERE qa_id = ?", (self.qa_id,)
            ) as cur:
                row = await cur.fetchone()

        if not row or row[0] == "closed":
            await interaction.response.send_message(
                "❌ This question is already closed.", ephemeral=True
            )
            return

        async with aiosqlite.connect("database.db") as db:
            await db.execute(
                "UPDATE qa_posts SET answerer_id = ?, status = 'answered' WHERE qa_id = ?",
                (interaction.user.id, self.qa_id)
            )
            await db.commit()

        asker = interaction.guild.get_member(self.asker_id)
        asker_mention = asker.mention if asker else "the asker"

        is_forum_thread = isinstance(interaction.channel, discord.Thread)

        if is_forum_thread:
            await interaction.response.send_message(
                f"💡 {interaction.user.mention} is answering! Post your answer here — images welcome! 📷\n"
                f"{asker_mention} — click ✅ **Mark Best Answer** on the original post above when satisfied."
            )
        else:
            thread = await interaction.message.create_thread(
                name=f"Answer #{self.qa_id} by {interaction.user.display_name}"
            )
            await thread.send(
                f"{interaction.user.mention} — post your answer here. Images welcome! 📷\n"
                f"{asker_mention} — click ✅ **Mark Best Answer** on the original post when satisfied."
            )
            await interaction.response.send_message(
                f"💡 Thread created!", ephemeral=True
            )

    async def mark_best(self, interaction: discord.Interaction):
        if interaction.user.id != self.asker_id:
            await interaction.response.send_message(
                "❌ Only the one who asked can mark the best answer.", ephemeral=True
            )
            return

        async with aiosqlite.connect("database.db") as db:
            async with db.execute(
                "SELECT status, answerer_id, question FROM qa_posts WHERE qa_id = ?",
                (self.qa_id,)
            ) as cur:
                row = await cur.fetchone()

            if not row or row[0] != "answered":
                await interaction.response.send_message(
                    "❌ No answer to mark yet.", ephemeral=True
                )
                return

            answerer_id = row[1]
            question = row[2]

            await db.execute(
                "UPDATE qa_posts SET status = 'closed' WHERE qa_id = ?",
                (self.qa_id,)
            )
            await db.commit()

        standing, rank_info = await award_standing(answerer_id, interaction.guild, QA_STANDING_ANSWER)
        await award_standing(self.asker_id, interaction.guild, QA_STANDING_ASK)

        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)

        rank_display = f"\n🎊 **<@{answerer_id}>** is now ranked **{rank_info[1]}**!" if rank_info else ""
        await interaction.response.send_message(
            f"✅ Best answer marked!\n"
            f"❓ **{question}**\n\n"
            f"<@{answerer_id}> earned **{QA_STANDING_ANSWER} Standing**\n"
            f"{interaction.user.mention} earned **{QA_STANDING_ASK} Standing** for asking"
            f"{rank_display}"
        )

class QuestView(discord.ui.View):
    def __init__(self, creator_id: int, quest_id: int):
        super().__init__(timeout=None)
        self.creator_id = creator_id
        self.quest_id   = quest_id

        accept_btn = discord.ui.Button(
            label="⚔️ Accept Quest",
            style=discord.ButtonStyle.green,
            custom_id=f"quest_accept_{quest_id}"
        )
        accept_btn.callback = self.accept
        self.add_item(accept_btn)

        success_btn = discord.ui.Button(
            label="✅ Mark Success",
            style=discord.ButtonStyle.blurple,
            custom_id=f"quest_success_{quest_id}"
        )
        success_btn.callback = self.success
        self.add_item(success_btn)

    async def accept(self, interaction: discord.Interaction):
        if interaction.user.id == self.creator_id:
            await interaction.response.send_message(
                "❌ You can't accept your own quest.", ephemeral=True
            )
            return

        async with aiosqlite.connect("database.db") as db:
            async with db.execute(
                "SELECT status FROM quests WHERE quest_id = ?",
                (self.quest_id,)
            ) as cur:
                row = await cur.fetchone()

            if not row or row[0] != "open":
                await interaction.response.send_message(
                    "❌ This quest is already taken or completed.", ephemeral=True
                )
                return

            await db.execute(
                "UPDATE quests SET status = 'accepted', acceptor_id = ? WHERE quest_id = ?",
                (interaction.user.id, self.quest_id)
            )
            await db.commit()

        for child in self.children:
            if child.custom_id == f"quest_accept_{self.quest_id}":
                child.disabled = True
        await interaction.message.edit(view=self)

        creator = interaction.guild.get_member(self.creator_id)
        creator_mention = creator.mention if creator else "the quest poster"
        await interaction.response.send_message(
            f"⚔️ {interaction.user.mention} has accepted the quest!\n"
            f"{creator_mention} — your quest was taken. Stand by for completion!"
        )

    async def success(self, interaction: discord.Interaction):
        if interaction.user.id != self.creator_id:
            await interaction.response.send_message(
                "❌ Only the quest creator can mark this done.", ephemeral=True
            )
            return

        async with aiosqlite.connect("database.db") as db:
            async with db.execute(
                "SELECT status, acceptor_id, description FROM quests WHERE quest_id = ?",
                (self.quest_id,)
            ) as cur:
                row = await cur.fetchone()

            if not row or row[0] != "accepted":
                await interaction.response.send_message(
                    "❌ Quest hasn't been accepted yet.", ephemeral=True
                )
                return

            acceptor_id = row[1]
            quest_description = row[2] if row[2] else "Unknown quest"

            await db.execute(
                "UPDATE quests SET status = 'done' WHERE quest_id = ?",
                (self.quest_id,)
            )
            await db.commit()

        standing, rank_info = await award_standing(acceptor_id, interaction.guild, QUEST_STANDING)
        await award_standing(self.creator_id, interaction.guild, 10)

        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)

        rank_display = f"\n🎊 **<@{acceptor_id}>** is now ranked **{rank_info[1]}**!" if rank_info else ""
        await interaction.response.send_message(
            f"✅ Quest complete!\n"
            f"📜 **{quest_description}**\n\n"
            f"<@{acceptor_id}> earned **{QUEST_STANDING} Standing**\n"
            f"{interaction.user.mention} earned **10 Standing** for posting"
            f"{rank_display}"
        )

# ── Setup hook (persistent buttons survive restart) ───────────────

async def setup_hook():
    async with aiosqlite.connect("database.db") as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                standing INTEGER DEFAULT 0
            )""")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS quests (
                quest_id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_id INTEGER,
                description TEXT,
                acceptor_id INTEGER,
                status TEXT DEFAULT 'open'
            )""")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                guild_id INTEGER PRIMARY KEY,
                quest_channel_id INTEGER,
                qa_channel_id INTEGER,
                news_channel_id INTEGER,
                last_news_id TEXT
            )""")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS qa_posts (
                qa_id INTEGER PRIMARY KEY AUTOINCREMENT,
                asker_id INTEGER,
                question TEXT,
                answerer_id INTEGER,
                status TEXT DEFAULT 'open'
            )""")
        await db.commit()

        async with db.execute(
            "SELECT quest_id, creator_id FROM quests WHERE status IN ('open','accepted')"
        ) as cur:
            rows = await cur.fetchall()

        async with db.execute(
            "SELECT qa_id, asker_id FROM qa_posts WHERE status IN ('open','answered')"
        ) as qa_cur:
            qa_rows = await qa_cur.fetchall()

    for quest_id, creator_id in rows:
        bot.add_view(QuestView(creator_id=creator_id, quest_id=quest_id))

    for qa_id, asker_id in qa_rows:
        bot.add_view(QAView(asker_id=asker_id, qa_id=qa_id))


bot.setup_hook = setup_hook

# ── on_ready ──────────────────────────────────────────────────────

@bot.event
async def on_ready():
    guild = discord.Object(id=int(os.environ.get("GUILD_ID", "0")))
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)
    if not check_warframe_news.is_running():
        check_warframe_news.start()
    print(f"{bot.user} is now online!")

# ── /quest command ────────────────────────────────────────────────

@bot.tree.command(name="quest", description="Post a new quest for others to accept")
@app_commands.describe(description="What do you need help with?")
async def quest(interaction: discord.Interaction, description: str):
    async with aiosqlite.connect("database.db") as db:
        cur = await db.execute(
            "INSERT INTO quests (creator_id, description) VALUES (?, ?)",
            (interaction.user.id, description)
        )
        quest_id = cur.lastrowid
        await db.commit()

    embed = discord.Embed(
        title="📜 New Quest",
        description=description,
        color=discord.Color.gold()
    )
    embed.set_footer(text=f"Posted by {interaction.user.display_name} • Quest #{quest_id}")

    view = QuestView(creator_id=interaction.user.id, quest_id=quest_id)

    async with aiosqlite.connect("database.db") as db:
        async with db.execute(
            "SELECT quest_channel_id FROM settings WHERE guild_id = ?",
            (interaction.guild.id,)
        ) as cur:
            row = await cur.fetchone()

    if row and row[0]:
        channel = interaction.guild.get_channel(row[0])
        await channel.send(embed=embed, view=view)
        await interaction.response.send_message(
            "📜 Your quest has been posted!", ephemeral=True
        )
    else:
        await interaction.response.send_message(embed=embed, view=view)

# ── /rank command ─────────────────────────────────────────────────

@bot.tree.command(name="mystatus", description="Check your Standing and rank progress")
async def mystatus(interaction: discord.Interaction):
    async with aiosqlite.connect("database.db") as db:
        async with db.execute(
            "SELECT standing FROM users WHERE user_id = ?",
            (interaction.user.id,)
        ) as cur:
            row = await cur.fetchone()

    standing = row[0] if row else 0
    rank_info = get_rank(standing)
    current_rank = rank_info[1] if rank_info else "Unranked"

    next_tier = None
    for threshold, name in reversed(RANK_TIERS):
        if threshold > standing:
            next_tier = (threshold, name)
            break

    embed = discord.Embed(
        title=f"⚔️ {interaction.user.display_name}",
        color=discord.Color.from_rgb(255, 177, 0)
    )
    embed.add_field(name="Rank", value=current_rank, inline=True)
    embed.add_field(name="Standing", value=f"{standing:,}", inline=True)

    if next_tier:
        needed = next_tier[0] - standing
        embed.add_field(
            name="Next Rank",
            value=f"{next_tier[1]} — {needed:,} more Standing needed",
            inline=False
        )
    else:
        embed.add_field(name="Next Rank", value="MAX — You are **Architect** 🔱", inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="leaderboard", description="Top 10 Tenno by Standing")
async def leaderboard(interaction: discord.Interaction):
    async with aiosqlite.connect("database.db") as db:
        async with db.execute(
            "SELECT user_id, standing FROM users ORDER BY standing DESC LIMIT 10"
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        await interaction.response.send_message(
            "No Tenno on the board yet. Complete some quests!", ephemeral=True
        )
        return

    medals = ["🥇", "🥈", "🥉"]
    embed = discord.Embed(
        title="🏆 Clan Standing — Top Tenno",
        color=discord.Color.from_rgb(255, 177, 0)
    )

    for i, (user_id, standing) in enumerate(rows):
        member = interaction.guild.get_member(user_id)
        name = member.display_name if member else "Unknown Tenno"
        rank_info = get_rank(standing)
        rank_name = rank_info[1] if rank_info else "Unranked"
        medal = medals[i] if i < 3 else f"**{i+1}.**"
        embed.add_field(
            name=f"{medal} {name}",
            value=f"{standing:,} Standing — *{rank_name}*",
            inline=False
        )

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="setquestchannel", description="Set the channel where quests are posted")
@app_commands.checks.has_permissions(administrator=True)
async def setquestchannel(interaction: discord.Interaction, channel: discord.abc.GuildChannel):
    async with aiosqlite.connect("database.db") as db:
        await db.execute(
            "INSERT INTO settings (guild_id, quest_channel_id) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET quest_channel_id = ?",
            (interaction.guild.id, channel.id, channel.id)
        )
        await db.commit()
    await interaction.response.send_message(
        f"✅ Quest channel set to {channel.mention}", ephemeral=True
    )

@bot.tree.command(name="setqachannel", description="Set the Forum channel for Q&A posts")
@app_commands.checks.has_permissions(administrator=True)
async def setqachannel(interaction: discord.Interaction, channel: discord.abc.GuildChannel):
    async with aiosqlite.connect("database.db") as db:
        await db.execute(
            "INSERT INTO settings (guild_id, qa_channel_id) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET qa_channel_id = ?",
            (interaction.guild.id, channel.id, channel.id)
        )
        await db.commit()
    await interaction.response.send_message(
        f"✅ Q&A channel set to {channel.mention}", ephemeral=True
    )


@bot.tree.command(name="ask", description="Post a question for the clan to answer")
@app_commands.describe(question="What do you want to know?")
async def ask(interaction: discord.Interaction, question: str):
    async with aiosqlite.connect("database.db") as db:
        cur = await db.execute(
            "INSERT INTO qa_posts (asker_id, question) VALUES (?, ?)",
            (interaction.user.id, question)
        )
        qa_id = cur.lastrowid
        await db.commit()

    embed = discord.Embed(
        title="❓ New Question",
        description=question,
        color=discord.Color.from_rgb(0, 168, 255)
    )
    embed.set_footer(text=f"Asked by {interaction.user.display_name} • Q #{qa_id}")

    view = QAView(asker_id=interaction.user.id, qa_id=qa_id)

    async with aiosqlite.connect("database.db") as db:
        async with db.execute(
            "SELECT qa_channel_id FROM settings WHERE guild_id = ?",
            (interaction.guild.id,)
        ) as cur:
            row = await cur.fetchone()

    if row and row[0]:
        channel = interaction.guild.get_channel(row[0])

        if isinstance(channel, discord.ForumChannel):
            thread_with_msg = await channel.create_thread(
                name=question[:100],
                embed=embed,
                view=view
            )
            await thread_with_msg.thread.send(
                f"{interaction.user.mention} — your question is posted! "
                f"Others will reply here. Click ✅ **Mark Best Answer** on the post above when done."
            )
        else:
            await channel.send(embed=embed, view=view)

        await interaction.response.send_message(
            "❓ Your question has been posted!", ephemeral=True
        )
    else:
        await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="setnewschannel", description="Set the channel for Warframe news")
@app_commands.checks.has_permissions(administrator=True)
async def setnewschannel(interaction: discord.Interaction, channel: discord.abc.GuildChannel):
    async with aiosqlite.connect("database.db") as db:
        await db.execute(
            "INSERT INTO settings (guild_id, news_channel_id) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET news_channel_id = ?",
            (interaction.guild.id, channel.id, channel.id)
        )
        await db.commit()
    await interaction.response.send_message(
        f"✅ News channel set to {channel.mention}", ephemeral=True
    )

@tasks.loop(minutes=15)
async def check_warframe_news():
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                "https://api.warframestat.us/pc/news?language=en",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    return
                news_items = await resp.json()
        except Exception:
            return

    async with aiosqlite.connect("database.db") as db:
        async with db.execute(
            "SELECT guild_id, news_channel_id, last_news_id FROM settings "
            "WHERE news_channel_id IS NOT NULL"
        ) as cur:
            rows = await cur.fetchall()

    for guild_id, news_channel_id, last_news_id in rows:
        guild = bot.get_guild(guild_id)
        if not guild:
            continue
        channel = guild.get_channel(news_channel_id)
        if not channel:
            continue

        new_items = []
        for item in news_items:
            if item["id"] == last_news_id:
                break
            new_items.append(item)

        if not new_items:
            continue

        for item in reversed(new_items):
            embed = discord.Embed(
                title=item.get("message", "Warframe News"),
                url=item.get("link", "https://www.warframe.com/news"),
                color=discord.Color.from_rgb(255, 177, 0)
            )
            if item.get("imageLink"):
                embed.set_image(url=item["imageLink"])
            embed.set_footer(text="📡 Warframe News")
            await channel.send(embed=embed)

        newest_id = new_items[0]["id"]
        async with aiosqlite.connect("database.db") as db:
            await db.execute(
                "UPDATE settings SET last_news_id = ? WHERE guild_id = ?",
                (newest_id, guild_id)
            )
            await db.commit()

bot.run(TOKEN)
