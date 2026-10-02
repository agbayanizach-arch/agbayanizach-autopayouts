import os
import json
import asyncio
from threading import Thread
from flask import Flask
import discord
from discord.ext import commands
from discord import app_commands

# -----------------------------
# Render keep-alive web server
# -----------------------------
app = Flask(__name__)

@app.get("/")
def home():
    return "Discord bot is online."

@app.get("/health")
def health():
    return {"status": "online"}

def start_web_server():
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, use_reloader=False)

# -----------------------------
# Files / persistent data
# -----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INVITE_FILE = os.path.join(BASE_DIR, "invite_data.json")
STOCK_FILE = os.path.join(BASE_DIR, "account_stock.json")
TICKET_FILE = os.path.join(BASE_DIR, "ticket_settings.json")

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default

def save_json(path, data):
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(temp, path)

invite_data = load_json(INVITE_FILE, {
    "stats": {},
    "member_inviter": {},
    "history": []
})
stock = load_json(STOCK_FILE, [])
ticket_settings = load_json(TICKET_FILE, {})

def user_key(guild_id, user_id):
    return f"{guild_id}:{user_id}"

def get_stats(guild_id, user_id):
    key = user_key(guild_id, user_id)
    stats = invite_data["stats"].setdefault(key, {
        "regular": 0,
        "leaves": 0,
        "fake": 0,
        "bonus": 0
    })
    for field in ("regular", "leaves", "fake", "bonus"):
        stats.setdefault(field, 0)
    return stats

def real_invites(guild_id, user_id):
    s = get_stats(guild_id, user_id)
    return max(0, s["regular"] + s["bonus"] - s["leaves"] - s["fake"])

def save_invites():
    save_json(INVITE_FILE, invite_data)

# -----------------------------
# Discord bot
# -----------------------------
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(
    command_prefix="-",
    intents=intents,
    help_command=None
)

# -----------------------------
# Ticket UI
# -----------------------------
class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Claim",
        emoji="🎉",
        style=discord.ButtonStyle.blurple,
        custom_id="ticket_claim"
    )
    async def claim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel

        # Automatically checks the ticket creator, equivalent to:
        # -i @ticket_creator
        if (
            channel is None
            or not isinstance(channel, discord.TextChannel)
            or not channel.topic
            or "ticket_creator_id=" not in channel.topic
        ):
            await interaction.response.send_message(
                "❌ This Claim button can only be used inside a created ticket.",
                ephemeral=True
            )
            return

        creator_id = None
        for part in channel.topic.split("|"):
            if part.startswith("ticket_creator_id="):
                creator_id = part.split("=", 1)[1]
                break

        try:
            creator_id = int(creator_id)
        except (TypeError, ValueError):
            await interaction.response.send_message(
                "❌ I couldn't find the ticket creator.",
                ephemeral=True
            )
            return

        stats = get_stats(interaction.guild.id, creator_id)
        real = real_invites(interaction.guild.id, creator_id)

        await interaction.response.send_message(
            f"🎉 **Claim / Payout Check**\n"
            f"Member: <@{creator_id}>\n"
            f"Real invites: **{real}**\n"
            f"Joins: **{stats['regular'] + stats['bonus']}**\n"
            f"Left: **{stats['leaves']}**\n"
            f"Fake: **{stats['fake']}**\n\n"
            f"💰 Use this result for the payout check.",
            ephemeral=True
        )

    @discord.ui.button(
        label="Close Ticket",
        emoji="🔒",
        style=discord.ButtonStyle.red,
        custom_id="ticket_close"
    )
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message(
                "❌ You need **Manage Channels** permission to close this ticket.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            "🔒 This ticket will be deleted in 5 seconds."
        )
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")
        except discord.HTTPException:
            pass


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Create ticket",
        emoji="🎟️",
        style=discord.ButtonStyle.green,
        custom_id="ticket_create"
    )
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        member = interaction.user

        if guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message(
                "❌ This can only be used in a server.",
                ephemeral=True
            )
            return

        existing = discord.utils.get(
            guild.text_channels,
            name=f"ticket-{member.id}"
        )
        if existing:
            await interaction.response.send_message(
                f"❌ You already have a ticket: {existing.mention}",
                ephemeral=True
            )
            return

        config = ticket_settings.get(str(guild.id), {})
        category_id = config.get("category_id")
        category = guild.get_channel(category_id) if category_id else None

        if category is None or not isinstance(category, discord.CategoryChannel):
            await interaction.response.send_message(
                "❌ Ticket setup is incomplete. Ask an administrator to run `/ticket-setup` again.",
                ephemeral=True
            )
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True
            )
        }

        for role_id in config.get("staff_role_ids", []):
            role = guild.get_role(int(role_id))
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                    embed_links=True,
                    manage_messages=True
                )

        if guild.me:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
                manage_channels=True,
                manage_messages=True
            )

        try:
            channel = await guild.create_text_channel(
                name=f"ticket-{member.id}",
                category=category,
                overwrites=overwrites,
                topic=f"ticket_creator_id={member.id}"
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I need **Manage Channels** permission to create tickets.",
                ephemeral=True
            )
            return

        # This counter belongs only to this bot's ticket system.
        # It is initialized/reset by /ticket-setup and never counts
        # tickets created by Ticket Tool or any other bot.
        config["ticket_count"] = int(config.get("ticket_count", 0)) + 1
        ticket_settings[str(guild.id)] = config
        save_json(TICKET_FILE, ticket_settings)

        # The claim description is shown ONLY inside the created ticket,
        # not on the public ticket panel.
        embed = discord.Embed(
            title=config.get("ticket_title", "Ticket Panel"),
            description=config.get(
                "ticket_message",
                f"Welcome {member.mention}! Please describe your issue and staff will help you."
            ),
            color=discord.Color.blurple()
        )

        claim_description = config.get("claim_description", "").strip()
        if claim_description:
            embed.add_field(
                name="🎉 Claim",
                value=claim_description[:1024],
                inline=False
            )

        embed.set_footer(text="Support Tickets")

        await channel.send(
            content=member.mention,
            embed=embed,
            view=TicketCloseView()
        )

        await interaction.response.send_message(
            f"✅ Your ticket has been created: {channel.mention}",
            ephemeral=True
        )

    @discord.ui.button(
        label="Claim",
        emoji="🎉",
        style=discord.ButtonStyle.blurple,
        custom_id="ticket_panel_claim"
    )
    async def panel_claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "🎉 **Claim is for payout checks.** Create/open the ticket first, then press **Claim** inside that ticket to automatically check the ticket creator's invites.",
            ephemeral=True
        )

    @discord.ui.button(
        label="Something else",
        emoji="❓",
        style=discord.ButtonStyle.gray,
        custom_id="ticket_other"
    )
    async def something_else(self, interaction: discord.Interaction, button: discord.ui.Button):
        # "Something else" uses the same private-ticket creation flow.
        await self.create_ticket.callback(interaction)


# -----------------------------
# Slash command 1
# -----------------------------
@bot.tree.command(
    name="ticket-setup",
    description="Set up the support ticket panel."
)
@app_commands.describe(
    title="Ticket panel title.",
    description="Text shown on the ticket panel.",
    claim_description="Text describing the claim/invite system."
)
@app_commands.checks.has_permissions(administrator=True)
async def ticket_setup(
    interaction: discord.Interaction,
    title: str,
    description: str,
    claim_description: str
):
    if interaction.guild is None or interaction.channel is None:
        await interaction.response.send_message(
            "❌ This command can only be used in a server.",
            ephemeral=True
        )
        return

    guild = interaction.guild
    me = guild.me
    if me is None:
        await interaction.response.send_message(
            "❌ I couldn't verify my bot permissions.",
            ephemeral=True
        )
        return

    required = (
        "view_channel",
        "send_messages",
        "embed_links",
        "manage_channels"
    )
    missing = [
        name.replace("_", " ").title()
        for name in required
        if not getattr(interaction.channel.permissions_for(me), name)
    ]
    if missing:
        await interaction.response.send_message(
            "❌ Missing: " + ", ".join(f"**{x}**" for x in missing),
            ephemeral=True
        )
        return

    category = discord.utils.get(guild.categories, name="Tickets")
    if category is None:
        try:
            category = await guild.create_category("Tickets")
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I need **Manage Channels** permission to create the Tickets category.",
                ephemeral=True
            )
            return

    # Reset the bot's own ticket counter whenever the panel is set up.
    # This is independent from Ticket Tool or tickets created by other bots.
    ticket_settings[str(guild.id)] = {
        "category_id": category.id,
        "staff_role_ids": [],
        "ticket_title": title[:256],
        "panel_description": description[:4000],
        "ticket_message": description[:4000],
        "claim_description": claim_description[:4000],
        "ticket_count": 0
    }
    save_json(TICKET_FILE, ticket_settings)

    # Public panel: keep the claim description OUT of this embed.
    embed = discord.Embed(
        title=title[:256],
        description=description[:4000],
        color=discord.Color.blurple()
    )
    embed.set_footer(text="Support Tickets")

    await interaction.channel.send(
        embed=embed,
        view=TicketPanelView()
    )
    await interaction.response.send_message(
        "✅ Ticket panel created.",
        ephemeral=True
    )

# -----------------------------
# Slash command 2
# -----------------------------
@bot.tree.command(
    name="restock",
    description="Add email:password accounts from a text file."
)
@app_commands.describe(file="A .txt file containing one email:password per line.")
@app_commands.checks.has_permissions(administrator=True)
async def restock(interaction: discord.Interaction, file: discord.Attachment):
    if not file.filename.lower().endswith((".txt", ".csv")):
        await interaction.response.send_message(
            "❌ Please upload a `.txt` or `.csv` file.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    try:
        raw = await file.read()
        text = raw.decode("utf-8", errors="replace")
    except Exception as exc:
        await interaction.followup.send(
            f"❌ Couldn't read the file: `{exc}`",
            ephemeral=True
        )
        return

    added = 0
    duplicates = 0
    invalid = 0
    existing = set(stock)

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            invalid += 1
            continue

        email, password = line.split(":", 1)
        email = email.strip()
        password = password.strip()

        if not email or not password:
            invalid += 1
            continue

        account = f"{email}:{password}"
        if account in existing:
            duplicates += 1
            continue

        stock.append(account)
        existing.add(account)
        added += 1

    save_json(STOCK_FILE, stock)

    await interaction.followup.send(
        f"✅ Restock complete.\n"
        f"Added: **{added}**\n"
        f"Duplicates: **{duplicates}**\n"
        f"Invalid lines: **{invalid}**\n"
        f"Total stock: **{len(stock)}**",
        ephemeral=True
    )

# -----------------------------
# Slash command 3
# -----------------------------
@bot.tree.command(
    name="payout",
    description="Pay the ticket creator when they reach a payout invite milestone."
)
@app_commands.describe(channel="The ticket channel to process.")
@app_commands.checks.has_permissions(administrator=True)
async def payout(interaction: discord.Interaction, channel: discord.TextChannel):
    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ This command can only be used in a server.",
            ephemeral=True
        )
        return

    topic = channel.topic or ""
    creator_id = None
    for part in topic.split("|"):
        if part.startswith("ticket_creator_id="):
            creator_id = part.split("=", 1)[1]
            break

    if not creator_id:
        await interaction.response.send_message(
            "❌ That channel is not a ticket with a stored creator.",
            ephemeral=True
        )
        return

    try:
        creator_id = int(creator_id)
    except ValueError:
        await interaction.response.send_message(
            "❌ The ticket creator ID is invalid.",
            ephemeral=True
        )
        return

    invites = real_invites(interaction.guild.id, creator_id)
    milestones = {2, 4, 6, 8, 10}

    if invites not in milestones:
        await interaction.response.send_message(
            f"❌ The ticket creator has **{invites}** real invites. "
            f"Payouts happen at exactly **2, 4, 6, 8, or 10**.",
            ephemeral=True
        )
        return

    if not stock:
        await interaction.response.send_message(
            "❌ Stock is empty. Restock first with `/restock`.",
            ephemeral=True
        )
        return

    account = stock.pop(0)
    save_json(STOCK_FILE, stock)

    member = interaction.guild.get_member(creator_id)
    if member is None:
        try:
            member = await interaction.guild.fetch_member(creator_id)
        except discord.HTTPException:
            member = None

    if member is None:
        stock.insert(0, account)
        save_json(STOCK_FILE, stock)
        await interaction.response.send_message(
            "❌ I couldn't find the ticket creator. Nothing was removed from stock.",
            ephemeral=True
        )
        return

    try:
        await member.send(
            f"🎁 **Payout**\n\n"
            f"You reached **{invites} real invites**.\n"
            f"Your account:\n`{account}`"
        )
    except (discord.Forbidden, discord.HTTPException):
        stock.insert(0, account)
        save_json(STOCK_FILE, stock)
        await interaction.response.send_message(
            "❌ I couldn't DM the ticket creator, so the account was returned to stock.",
            ephemeral=True
        )
        return

    stats = get_stats(interaction.guild.id, creator_id)
    stats.update({"regular": 0, "leaves": 0, "fake": 0, "bonus": 0})
    save_invites()

    await interaction.response.send_message(
        f"✅ Payout sent to <@{creator_id}>.\n"
        f"Milestone: **{invites} invites**\n"
        f"Remaining stock: **{len(stock)}**",
        ephemeral=True
    )

# -----------------------------
# Prefix command 1
# -----------------------------
@bot.command(name="i", aliases=["invites"])
async def invites(ctx: commands.Context, member: discord.Member | None = None):
    if ctx.guild is None:
        return

    target = member or ctx.author
    stats = get_stats(ctx.guild.id, target.id)
    real = real_invites(ctx.guild.id, target.id)

    embed = discord.Embed(
        title="Invite log",
        description=f"➤ **{target.display_name} has {real} invites**",
        color=discord.Color.blurple()
    )
    embed.add_field(
        name="Joins",
        value=str(stats["regular"] + stats["bonus"]),
        inline=True
    )
    embed.add_field(name="Left", value=str(stats["leaves"]), inline=True)
    embed.add_field(name="Fake", value=str(stats["fake"]), inline=True)
    embed.add_field(name="Rejoins", value="0 (7d)", inline=True)
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.set_footer(text=f"Requested by {ctx.author.display_name}")

    await ctx.send(embed=embed)

# -----------------------------
# Prefix command 2
# -----------------------------
@bot.command(name="resetinvites")
@commands.has_permissions(administrator=True)
async def reset_invites(ctx: commands.Context, member: discord.Member | None = None):
    if ctx.guild is None:
        return

    if member:
        key = user_key(ctx.guild.id, member.id)
        invite_data["stats"][key] = {
            "regular": 0,
            "leaves": 0,
            "fake": 0,
            "bonus": 0
        }
        invite_data["member_inviter"].pop(key, None)
        invite_data["history"] = [
            x for x in invite_data.get("history", [])
            if str(x) != key
        ]
        save_invites()
        await ctx.send(
            f"✅ Successfully reset invite data for **{member.display_name}**."
        )
    else:
        prefix = f"{ctx.guild.id}:"
        invite_data["stats"] = {
            k: v for k, v in invite_data["stats"].items()
            if not str(k).startswith(prefix)
        }
        invite_data["member_inviter"] = {
            k: v for k, v in invite_data["member_inviter"].items()
            if not str(k).startswith(prefix)
        }
        invite_data["history"] = [
            x for x in invite_data.get("history", [])
            if not str(x).startswith(prefix)
        ]
        save_invites()
        await ctx.send("✅ Successfully reset all invite data and history.")

# -----------------------------
# Invite tracking
# -----------------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")
    print(f"Connected to {len(bot.guilds)} server(s).")

@bot.event
async def on_member_join(member: discord.Member):
    # Invite attribution is refreshed by comparing cached invite uses.
    try:
        before = bot._invite_cache.get(member.guild.id, {})
        current = {inv.code: inv.uses for inv in await member.guild.invites()}

        used_code = None
        for code, uses in current.items():
            if uses > before.get(code, 0):
                used_code = code
                break

        bot._invite_cache[member.guild.id] = current

        if used_code:
            invites = await member.guild.invites()
            invite = discord.utils.get(invites, code=used_code)
            if invite and invite.inviter and invite.inviter.id != member.id:
                inviter = invite.inviter
                stats = get_stats(member.guild.id, inviter.id)
                stats["regular"] += 1
                invite_data["member_inviter"][user_key(member.guild.id, member.id)] = inviter.id
                save_invites()
    except (discord.Forbidden, discord.HTTPException):
        pass

@bot.event
async def on_member_remove(member: discord.Member):
    key = user_key(member.guild.id, member.id)
    inviter_id = invite_data.get("member_inviter", {}).get(key)
    if inviter_id:
        stats = get_stats(member.guild.id, int(inviter_id))
        stats["leaves"] += 1
        save_invites()

# -----------------------------
# Errors + startup
# -----------------------------
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use that command.", delete_after=5)
        return
    if isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ I couldn't find that member.", delete_after=5)
        return
    print(f"Prefix command error: {repr(error)}")

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        message = "❌ You need Administrator permission to use this command."
    else:
        print(f"Slash command error: {repr(error)}")
        message = "❌ An error occurred while running that command."

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)

async def sync_commands():
    try:
        # If GUILD_ID is set in Render, sync to that server first so commands
        # appear there almost immediately while also syncing globally.
        guild_id = os.getenv("GUILD_ID")
        if guild_id:
            try:
                guild = discord.Object(id=int(guild_id))
                guild_synced = await bot.tree.sync(guild=guild)
                print(f"Synced {len(guild_synced)} guild slash command(s) to {guild_id}.")
            except (ValueError, discord.HTTPException) as exc:
                print(f"Guild slash-command sync failed: {exc}")

        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} global slash command(s).")
        print("Slash commands:", ", ".join(cmd.name for cmd in bot.tree.get_commands()))
    except Exception as exc:
        print(f"Global slash-command sync failed: {exc}")

async def setup():
    bot._invite_cache = {}

    # Migrate old ticket settings: only this bot's own ticket counter is used.
    for guild_id, config in ticket_settings.items():
        if "ticket_count" not in config:
            config["ticket_count"] = 0
    save_json(TICKET_FILE, ticket_settings)

    # Persistent views make ticket buttons continue working after a restart.
    bot.add_view(TicketPanelView())
    bot.add_view(TicketCloseView())

    # Cache invites before normal operation.
    for guild in bot.guilds:
        try:
            invites = await guild.invites()
            bot._invite_cache[guild.id] = {
                inv.code: inv.uses for inv in invites
            }
        except (discord.Forbidden, discord.HTTPException):
            bot._invite_cache[guild.id] = {}

    await sync_commands()

@bot.event
async def setup_hook():
    await setup()

def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError(
            "DISCORD_TOKEN is missing. Add it in Render Environment Variables."
        )

    Thread(target=start_web_server, daemon=True).start()
    bot.run(token)

if __name__ == "__main__":
    main()
