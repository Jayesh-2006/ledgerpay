# LedgerPay architecture

Start with a wallet lookup, then follow a funding request and a transfer. Use the [Postman collection](../LedgerPay.postman_collection.json) to send each request while you read the code.

## 1. Follow an HTTP request

A client sends an HTTP method, path, headers, and sometimes a JSON body. Uvicorn runs the server and passes each request to FastAPI. The server routes it to a Python function, runs that function, and returns a status code and body to the client.

Try **Health** and **Create sender wallet** in Postman. `GET /health` reads process status. `POST /api/v1/wallets` with `{"owner_name":"Asha"}` creates a wallet and returns `201`. **Get sender wallet** uses the returned ID in the path and returns `200`. A missing ID returns `404`. Sending a blank name returns `422` before a wallet is created.

- [app/main.py](../app/main.py) builds the FastAPI app and mounts the versioned routers. An endpoint is a method and path combination, such as `GET /health`.
- [app/api/wallets.py](../app/api/wallets.py) defines the wallet endpoints. GET reads state. POST creates a resource or requests a state change.
- [app/schemas.py](../app/schemas.py) defines Pydantic models. Without validation a blank owner or a fractional paise value could reach the database code. Response models define the fields clients see.
- `/openapi.json` describes the paths, headers and data shapes. Swagger UI at `/docs` uses that description to render an interactive client. [FastAPI request body documentation](https://fastapi.tiangolo.com/tutorial/body/)

## 2. Follow a wallet into PostgreSQL

Try **List wallets**, **Fund sender with simulated INR**, and **Get funded balance**. The collection funds the sender with `100000` paise, which is ₹1,000.00.

[app/database.py](../app/database.py) configures the engine and provides a short-lived SQLAlchemy session for each request. The engine manages database connections. The session tracks ORM objects and coordinates their SQL work. It closes at the end of the request so an unfinished read transaction and its connection are released.

[app/models.py](../app/models.py) maps Python classes to tables. A table stores rows with named columns. A primary key identifies one row. A foreign key requires a related row to exist. For example, `wallet_transactions.wallet_id` refers to an existing wallet.

When `create_wallet` adds a `Wallet` and flushes the session, SQLAlchemy issues an `INSERT` and reads back the database-generated ID, balance, and timestamp. A wallet lookup uses a primary-key `SELECT`. The ORM builds parameterized SQL. You can still inspect and reason about the underlying statements.

Funding runs through [app/services/payments.py](../app/services/payments.py). It locks the wallet, checks the credit against the cap, updates the wallet balance, and inserts a `fund` history row. The balance update and history insert commit together. If a statement fails, PostgreSQL rolls back the transaction so no partial credit persists.

A database check constraint protects the balance range even when a write bypasses the API. The `(wallet_id, id)` history index lets PostgreSQL locate a wallet's rows and scan backward in ID order. For example, `limit=1&offset=0` asks for the newest entry. The primary keys support individual wallet and transfer lookups.

## 3. Follow a transfer and its locks

Try **Transfer to receiver**, **Get transfer details**, and **Get latest sender transaction**. The collection transfers `50000` paise from the sender to the receiver. The sender and receiver each end with `50000` paise.

[app/api/transfers.py](../app/api/transfers.py) receives the body. The Pydantic schema rejects identical wallet IDs or invalid amounts. The route calls `transfer_funds` in [app/services/payments.py](../app/services/payments.py).

1. `with session.begin()` starts the transaction. The service reserves the idempotency key inside this block.
2. `lock_wallets` selects both rows in ascending wallet ID order with `FOR UPDATE`. Both wallets stay locked until the transaction ends.
3. The service checks the sender's available balance and the receiver's remaining capacity.
4. It changes both balances and inserts a transfer row. `flush()` sends pending changes and obtains the transfer ID while the transaction is still open.
5. It adds `transfer_out` and `transfer_in` history rows linked to that transfer. Leaving the block successfully commits every write. An exception rolls them back.

### Two payments competing for the same balance

Wallet A has ₹1,000. Request 1 tries to transfer ₹800 from A to B. Request 2 tries to transfer ₹800 from A to C. Assume Request 1 obtains the lock on A first.

| Moment | Request 1 | Request 2 |
| --- | --- | --- |
| Lock wallet A | Reads `100000` paise under the lock | Waits for A's row lock |
| Apply payment | Writes a debit of `80000` and credits B | Still waiting |
| Finish | Commits and releases locks. A now holds `20000`. | Acquires the lock and reads the updated `20000`. |
| Respond | Returns `201` | Rolls back and returns `409 insufficient_funds`. C stays at zero. |

Without locks, both requests could read `100000` before either writes and both approve the debit. Even if both set A's balance to `20000`, one debit would be lost while both receivers could be credited. The positive-balance constraint alone would miss that error. Locking in ascending order also means that A→B and B→A ask for the same first row, avoiding the cycle where each holds the row the other needs. [PostgreSQL row locking and deadlocks](https://www.postgresql.org/docs/17/explicit-locking.html)

| ACID property | What it means here |
| --- | --- |
| Atomicity | Debit, credit, transfer, history, and replay record all commit or all roll back. |
| Consistency | Validation and constraints preserve the rules. A successful internal transfer conserves the combined balance of the two wallets. |
| Isolation | Row locks make concurrent balance writers wait. The app uses Read Committed isolation. |
| Durability | PostgreSQL persists committed data. Restarting Uvicorn opens new connections to the existing rows. |

[Test transfers](../tests/test_transfers.py) uses two HTTP requests and verifies both actually wait in PostgreSQL before releasing the blocking lock. A second test adds a temporary database constraint that rejects transfer history, then verifies rollback of balances, transfer, history, and the key reservation.

## 4. Follow a safe retry

Run **Replay funding** or **Replay transfer** after the original request. Both send the original client ID, key, and body. **Conflict for changed transfer amount** keeps the key and changes the amount. It returns `409`.

[app/api/payment_headers.py](../app/api/payment_headers.py) parses the two required headers. [app/services/idempotency.py](../app/services/idempotency.py) sorts and serializes the validated request fields, then hashes them with SHA-256. Funding also includes the wallet ID from the path. JSON field order and spacing do not affect the hash.

The composite primary key on `idempotency_records` enforces uniqueness across client ID, operation, and key. The service attempts `INSERT ... ON CONFLICT DO NOTHING RETURNING` before locking wallets. The result has two possible paths.

- If the insert returns a row, this request owns the reservation. It moves the money and saves the response in that row. The reservation and payment become visible together at commit.
- If the insert returns no row, the service reads the committed record. A matching hash replays the original JSON with `201`. A different hash returns `409` before a wallet write.

If a second request arrives before the first commits, PostgreSQL waits on the uniqueness decision. If the first commits, the separate `SELECT` gets a fresh snapshot under Read Committed and sees the saved result. If the first rolls back, the second can acquire the reservation and attempt the payment. [PostgreSQL Read Committed isolation](https://www.postgresql.org/docs/current/transaction-iso.html)

Imagine the server commits a ₹500 transfer but the network drops the response. Without a stored key, the retry would look like a second ₹500 transfer. With the same key, it returns the original transfer ID and timestamp. A funding replay similarly returns its original balance even if the wallet balance has changed since then.

Invalid requests never enter the payment service. Business failures and SQL errors roll back the reservation along with any other writes. If the client receives an ambiguous server or network error, it can retry the same request and let the committed record decide whether to replay or execute. [SQLAlchemy transaction context managers](https://docs.sqlalchemy.org/en/20/orm/session_basics.html#framing-out-a-begin-commit-rollback-block)

The demo client ID is an arbitrary string the caller supplies. A real payment service would derive that scope from an authenticated account and verify that the account owns the wallet.
