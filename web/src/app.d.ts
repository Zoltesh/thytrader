// See https://svelte.dev/docs/kit/types#app.d.ts
// for information about these interfaces
declare global {
	namespace App {
		// interface Error {}
		// interface Locals {}
		interface PageData {
			/** Static environment/context pill in the shared topbar. Not a health signal. */
			contextLabel: string;
		}
		// interface PageState {}
		// interface Platform {}
	}
}

export {};
