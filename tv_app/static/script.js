document.addEventListener('DOMContentLoaded', function() {
    const searchForm = document.querySelector('.search-form');
    const searchInput = document.querySelector('.search-input');
    const searchButton = document.querySelector('.search-button');
    const searchIconButton = document.querySelector('.search-icon-button');

    searchIconButton.addEventListener('click', function(event){
        event.preventDefault();
         if (window.innerWidth <= 768) { // Check if we're in the small-screen state
                // Show the input, hide the icon
                searchInput.style.display = 'inline-block';
                searchButton.style.display = 'inline-block';
                searchIconButton.style.display = 'none';
                searchInput.focus(); // Put the cursor in the input
        }
    });
	// Initialize slideshow
    let slideIndex = 1; // Start at 1 for centering
    showSlides(slideIndex);

    function plusSlides(n) {
        showSlides(slideIndex += n);
    }

	function showSlides(n) {
		let i;
		let slides = document.getElementsByClassName("mySlides");
        let slideshowInner = document.querySelector(".slideshow-inner");

		if (n > slides.length) { slideIndex = 1; }
		if (n < 1) { slideIndex = slides.length; }

        // Remove 'active-slide' class from all slides and apply default transform
          for (i = 0; i < slides.length; i++) {
            slides[i].classList.remove("active-slide");
          }

        // Add 'active-slide' class to the current slide
        if (slides.length > 0) {
            slides[slideIndex - 1].classList.add("active-slide");
        }
        // Calculate transform based on active slide.  Center the active slide.
        let offset = (slideIndex - 1) * (100/3);  // 100 / number of visible slides
        slideshowInner.style.transform = `translateX(${-offset}%)`;
	}
     // Automatic slideshow advance
    let slideInterval = setInterval(() => { plusSlides(1); }, 5000);

     // Pause slideshow on hover
    const slideshowContainer = document.querySelector(".slideshow-container");
    if(slideshowContainer){ //Ensure that the element exist before adding event listeners
        slideshowContainer.addEventListener('mouseover', () => {
            clearInterval(slideInterval);
        });

        slideshowContainer.addEventListener('mouseout', () => {
            slideInterval = setInterval(() => { plusSlides(1); }, 5000);
        });
    }
});
