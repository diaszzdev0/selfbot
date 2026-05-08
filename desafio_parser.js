/**
 * Selfbot Discord - Parser Desafios
 * Processa mensagens 1x1, 2x2, 3x3, 4x4 + Gel Inf/Normal + Valor + Jogadores
 */

function processarDesafio(mensagem) {
  const resultado = {
    modalidade: null,
    gelo_infinito: false,
    valor: null,
    jogadores: []
  };

  // 1. Modalidade
  const modMatch = mensagem.match(/^(1x1|2x2|3x3|4x4)/i);
  if (modMatch) resultado.modalidade = modMatch[1].toUpperCase();

  // 2. Gelo
  if (mensagem.includes('Gel Inf')) resultado.gelo_infinito = true;
  else if (mensagem.includes('Gel Normal')) resultado.gelo_infinito = false;

  // 3. Valor R$ X,XX
  const valMatch = mensagem.match(/R\$\s*([\d,]+)/i);
  if (valMatch) resultado.valor = valMatch[1];

  // 4. Jogadores @nome
  const jogMatch = mensagem.match(/@([\w\s]+)/g);
  if (jogMatch) {
    resultado.jogadores = jogMatch.map(j => j.slice(1).trim());
  }

  return resultado;
}

// Testes
console.log(processarDesafio("1x1 Mobile - Gel Normal | R$ 1,00 @j1 @j2"));
// → {modalidade: "1X1", gelo_infinito: false, valor: "1,00", jogadores: ["j1", "j2"]}

console.log(processarDesafio("4x4 Gel Inf | R$ 5,50"));
// → {modalidade: "4X4", gelo_infinito: true, valor: "5,50", jogadores: []}

// Uso selfbot Discord.js
// client.on('messageCreate', msg => {
//   const desafio = processarDesafio(msg.content);
//   if (desafio.modalidade) {
//     // Criar sala modo gelo_infinito
//     console.log(desafio);
//   }
// });

/*
Uso no Discord DevTools:
1. F12 → Console
2. Cole código
3. Teste: processarDesafio("mensagem")
*/
