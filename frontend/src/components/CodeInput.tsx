import { useState } from 'react';

interface Props {
  onSubmit: (code: string, language?: string) => void;
  disabled: boolean;
}

const SAMPLE_CODE = `def get_user(user_id):
    query = "SELECT * FROM users WHERE id = " + user_id
    cursor.execute(query)
    return cursor.fetchone()

def process_orders(customer_id):
    orders = db.query("SELECT * FROM orders")
    for order in orders:
        items = db.query(f"SELECT * FROM items WHERE order_id = {order.id}")
        total = 0
        for item in items:
            total += item.price * item.quantity
        order.total = total
    return orders

SECRET_KEY = "sk-proj-abc123def456ghi789jkl"
`;

export default function CodeInput({ onSubmit, disabled }: Props) {
  const [code, setCode] = useState('');
  const [language, setLanguage] = useState('');

  const handleSubmit = () => {
    if (code.trim()) {
      onSubmit(code, language || undefined);
    }
  };

  const loadSample = () => {
    setCode(SAMPLE_CODE.trim());
    setLanguage('python');
  };

  return (
    <div className="code-input">
      <div className="input-header">
        <h2>Submit Code for Review</h2>
        <div className="input-actions">
          <input
            type="text"
            className="lang-input"
            placeholder="Language (auto-detect)"
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            disabled={disabled}
          />
          <button className="btn-secondary" onClick={loadSample} disabled={disabled}>
            Load Sample
          </button>
          <button className="btn-primary" onClick={handleSubmit} disabled={disabled || !code.trim()}>
            Start Review
          </button>
        </div>
      </div>
      <textarea
        className="code-textarea"
        value={code}
        onChange={(e) => setCode(e.target.value)}
        placeholder="Paste your code here..."
        disabled={disabled}
        spellCheck={false}
      />
    </div>
  );
}
