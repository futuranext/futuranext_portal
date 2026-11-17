// Executado pela imagem oficial do Mongo somente na criação do volume.
// Cria o usuário da aplicação com leitura e escrita apenas no banco platform.
const platform = db.getSiblingDB('platform');
if (!platform.getUser('platform_app')) {
  platform.createUser({
    user: 'platform_app',
    pwd: process.env.APP_MONGO_PASSWORD,
    roles: [{role: 'readWrite', db: 'platform'}],
  });
}
