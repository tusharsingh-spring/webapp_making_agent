// Get elements from the board and button
const cells = document.querySelectorAll('.cell');
const resetButton = document.querySelector('.reset-button');

// Initialize variables for game state
let currentPlayer = 'X';
let gameOver = false;

// Function to handle cell click
function handleCellClick(event) {
    if (gameOver) return;
    const cell = event.target;
    if (cell.textContent !== '') return;

    // Update cell content and game state
    cell.textContent = currentPlayer;
    if (checkForWin(currentPlayer)) {
        gameOver = true;
        resetButton.textContent = `Game Over! ${currentPlayer} wins.`;
    } else if (checkForDraw()) {
        gameOver = true;
        resetButton.textContent = 'It\'s a draw!';
    } else {
        currentPlayer = currentPlayer === 'X' ? 'O' : 'X';
    }
}

// Function to check for a win
function checkForWin(player) {
    const winningCombinations = [
        [0, 1, 2],
        [3, 4, 5],
        [6, 7, 8],
        [0, 3, 6],
        [1, 4, 7],
        [2, 5, 8],
        [0, 4, 8],
        [2, 4, 6],
    ];

    for (const combination of winningCombinations) {
        if (
            cells[combination[0]].textContent === player &&
            cells[combination[1]].textContent === player &&
            cells[combination[2]].textContent === player
        ) {
            return true;
        }
    }

    return false;
}

// Function to check for a draw
function checkForDraw() {
    for (const cell of cells) {
        if (cell.textContent === '') return false;
    }
    return true;
}

// Add event listeners to cells and button
cells.forEach((cell) => cell.addEventListener('click', handleCellClick));
resetButton.addEventListener('click', resetGame);

// Function to reset the game
function resetGame() {
    cells.forEach((cell) => (cell.textContent = ''));
    currentPlayer = 'X';
    gameOver = false;
    resetButton.textContent = 'Reset Game';
}

// Initialize the game
resetGame();