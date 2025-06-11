Hooks.once('init', () => {
  if (typeof Babele !== 'undefined') {
    game.babele.register({
      module: 'lang-dnd-premium', // Identyfikator twojego modułu
      lang: 'pl', // Język
      dir: 'lang/pl', // Katalog z tłumaczeniami
    });
  }
});