// Configuração do PM2:  pm2 start ecosystem.config.js && pm2 save
module.exports = {
  apps: [
    {
      name: "celulares",
      script: "run.py",
      interpreter: "./.venv/bin/python",
      cwd: __dirname,
      env: { PYTHONUNBUFFERED: "1" },
      max_memory_restart: "400M",
      time: true,
    },
  ],
};
