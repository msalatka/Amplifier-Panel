const eslint = require('@eslint/js')
const globals = require('globals')

module.exports = [
	{
		ignores: ['packaging/vendor/**'],
	},
	eslint.configs.recommended,
	{
		files: ['static/js/**/*.js'],
		languageOptions: {
			ecmaVersion: 2022,
			sourceType: 'script',
			globals: {
				...globals.browser,
				Chart: 'readonly',
			},
		},
		rules: {
			'no-unused-vars': ['error', {argsIgnorePattern: '^_'}],
			'no-constant-condition': ['error', {checkLoops: false}],
		},
	},
	{
		files: ['static/js/dashboard*.js'],
		rules: {
			// These classic scripts intentionally share one browser-global scope and are
			// loaded in the order defined at the end of templates/index.html.
			'no-undef': 'off',
			'no-unused-vars': 'off',
		},
	},
]
