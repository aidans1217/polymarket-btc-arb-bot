# Polymarket BTC 15-Minute Arbitrage Bot

A Python bot that monitors Polymarket's 15-minute BTC Up/Down 
prediction markets and automatically executes arbitrage trades 
when combined prices fall below $1.00.

## How It Works
Every 15 minutes, Polymarket opens a market asking "Will BTC go 
Up or Down?" Each correct share pays $1.00. If the combined cost 
of buying both Up and Down drops below $1.00, one side must win — 
guaranteeing profit regardless of BTC's direction.

## Features
- Real-time price scanning via Polymarket REST APIs
- Automated dual-leg order execution
- Slippage protection
- Dry-run mode for safe testing
- Session performance tracker

## Tech Stack
- Python
- REST APIs (Polymarket Gamma + CLOB)
- py-clob-client
- python-dotenv

## Setup
1. Install dependencies:
pip install requests python-dotenv eth-account py-clob-client

2. Create a .env file:
POLY_PRIVATE_KEY=0xYourPrivateKeyHere
POLY_PROXY_ADDRESS=0xYourPolymarketProxyWalletAddress

3. Run in dry-run mode first (DRY_RUN = True in settings)

4. Run the bot:
python btc_arb_bot.py

## ⚠️ Disclaimer
This is for educational purposes. Use at your own risk.
