import { useEffect, useState } from 'react'
import './App.css'

function App() {
  const [health, setHealth] = useState<'checking' | 'ok' | 'error'>('checking')

  useEffect(() => {
    fetch('/api/health')
      .then((res) => (res.ok ? res.json() : Promise.reject(res)))
      .then((data) => setHealth(data.status === 'ok' ? 'ok' : 'error'))
      .catch(() => setHealth('error'))
  }, [])

  return (
    <main>
      <h1>dispatcher</h1>
      <p>N3 Vocab &amp; Kanji Batch Manager</p>
      <p>Backend status: {health}</p>
    </main>
  )
}

export default App
