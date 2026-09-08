import { Route, Routes } from 'react-router-dom'
import './App.css'
import Header from './components/Header/Header';
import Home from './components/Home/Home';
import Dashboard from './components/Dashboard/Dashboard';
import WebsiteDetail from './components/WebsiteDetail/WebsiteDetail';


function App() {
  return (
    <div className="app">
      <Header/>

      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/websites/:websiteId" element={<WebsiteDetail />} />
      </Routes>
    </div>
  )
}

export default App
