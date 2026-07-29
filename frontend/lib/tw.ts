/**
 * Helpers para conflitos de utilitários do Tailwind.
 *
 * O `clsx` só concatena classes — não resolve conflito entre utilitários do
 * mesmo grupo. Quem decide é a ordem no CSS gerado, não a ordem no atributo:
 * o Tailwind emite `w-full` DEPOIS de `w-44`, então um componente com
 * `w-full` na base vencia a largura passada pelo chamador, e o campo esticava
 * pela linha inteira.
 */

/**
 * Retorna 'w-full' apenas se o chamador não tiver definido uma largura.
 *
 * Reconhece também variantes (`sm:w-80`, `lg:max-w-md`) e o modificador
 * important (`!w-44`) — senão um `sm:w-80` do chamador conviveria com o
 * `w-full` da base e o campo esticaria em telas pequenas.
 */
export function larguraPadrao(className?: string): string {
  const temLargura = /(?:^|\s)!?(?:[\w-]+:)*(?:w-|max-w-|min-w-)\S/.test(className ?? '');
  return temLargura ? '' : 'w-full';
}
