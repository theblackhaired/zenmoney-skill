import type { Account, Transaction, Tag, Instrument, Budget, Reminder, Merchant } from '../api/types.js';

/** Format account for display */
export function formatAccount(account: Account, instruments: Map<number, Instrument>): Record<string, unknown> {
  const currency = instruments.get(account.instrument);
  return {
    id: account.id,
    title: account.title,
    type: account.type,
    balance: account.balance,
    currency: currency?.shortTitle ?? 'Unknown',
    inBalance: account.inBalance,
    creditLimit: account.creditLimit || undefined,
    archived: account.archive || undefined,
    company: undefined, // resolve later if needed
  };
}

/** Format transaction for display */
export function formatTransaction(
  tx: Transaction,
  accounts: Map<string, Account>,
  tags: Map<string, Tag>,
  instruments: Map<number, Instrument>,
  merchants: Map<string, Merchant>,
): Record<string, unknown> {
  const outcomeAccount = accounts.get(tx.outcomeAccount);
  const incomeAccount = accounts.get(tx.incomeAccount);
  const outCurrency = instruments.get(tx.outcomeInstrument);
  const inCurrency = instruments.get(tx.incomeInstrument);
  const isTransfer = tx.outcomeAccount !== tx.incomeAccount && tx.outcome > 0 && tx.income > 0;
  const isExpense = tx.outcome > 0 && !isTransfer;
  const isIncome = tx.income > 0 && !isTransfer;

  const categories = tx.tag?.map(id => tags.get(id)?.title).filter(Boolean) ?? [];
  const merchant = tx.merchant ? merchants.get(tx.merchant)?.title : undefined;

  const result: Record<string, unknown> = {
    id: tx.id,
    date: tx.date,
    type: isTransfer ? 'transfer' : isExpense ? 'expense' : 'income',
  };

  if (isExpense) {
    result.amount = tx.outcome;
    result.currency = outCurrency?.shortTitle;
    result.account = outcomeAccount?.title;
  } else if (isIncome) {
    result.amount = tx.income;
    result.currency = inCurrency?.shortTitle;
    result.account = incomeAccount?.title;
  } else {
    result.outcomeAmount = tx.outcome;
    result.outcomeCurrency = outCurrency?.shortTitle;
    result.fromAccount = outcomeAccount?.title;
    result.incomeAmount = tx.income;
    result.incomeCurrency = inCurrency?.shortTitle;
    result.toAccount = incomeAccount?.title;
  }

  if (categories.length) result.categories = categories;
  if (merchant) result.merchant = merchant;
  if (tx.payee) result.payee = tx.payee;
  if (tx.comment) result.comment = tx.comment;
  if (tx.hold) result.hold = true;
  if (tx.deleted) result.deleted = true;

  return result;
}

/** Format budget for display */
export function formatBudget(budget: Budget, tags: Map<string, Tag>): Record<string, unknown> {
  const tag = budget.tag ? tags.get(budget.tag) : null;
  return {
    category: tag?.title ?? (budget.tag === null ? 'Uncategorized' : 'Total'),
    month: budget.date,
    income: budget.income,
    incomeLock: budget.incomeLock,
    outcome: budget.outcome,
    outcomeLock: budget.outcomeLock,
  };
}

/** Format reminder for display */
export function formatReminder(
  reminder: Reminder,
  accounts: Map<string, Account>,
  tags: Map<string, Tag>,
): Record<string, unknown> {
  const incomeAccount = accounts.get(reminder.incomeAccount);
  const outcomeAccount = accounts.get(reminder.outcomeAccount);
  const categories = reminder.tag?.map(id => tags.get(id)?.title).filter(Boolean) ?? [];

  return {
    id: reminder.id,
    payee: reminder.payee,
    comment: reminder.comment,
    income: reminder.income || undefined,
    outcome: reminder.outcome || undefined,
    fromAccount: outcomeAccount?.title,
    toAccount: incomeAccount?.title,
    categories: categories.length ? categories : undefined,
    interval: reminder.interval,
    step: reminder.step,
    startDate: reminder.startDate,
    endDate: reminder.endDate,
    notify: reminder.notify,
  };
}

/** Truncate array with message */
export function truncateResults<T>(items: T[], limit: number): { items: T[]; truncated: boolean; total: number } {
  return {
    items: items.slice(0, limit),
    truncated: items.length > limit,
    total: items.length,
  };
}
